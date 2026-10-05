"""Definition state endpoints (spec "Definition state endpoints")."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import schedule_posting as posting
from app.services import schedule_read
from app.services.errors import ConflictError
from app.services.moze_import_service import IMPORT_LOCK_KEY


def _rows(db, definition) -> list[ScheduleInstance]:
    db.expire_all()
    return list(
        db.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id).order_by(ScheduleInstance.seq))
    )


@pytest.fixture()
def card(seed):
    return seed.account("範例卡")


def test_resume_skips_the_paused_months_by_default(client, db_session, seed, card, today):
    # Spec "Resume skips the paused months by default".
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")], status="paused")
    for seq, day in enumerate((date(2026, 7, 22), date(2026, 8, 22), date(2026, 9, 22), date(2026, 10, 22)), start=1):
        seed.instance(netflix, seq, day)
    db_session.commit()

    response = client.post(f"/schedules/definitions/{netflix.id}/resume")

    assert response.status_code == 200 and response.json()["status"] == "active"
    rows = _rows(db_session, netflix)
    assert [(row.status, row.acted_by) for row in rows[:3]] == [("skipped", "owner")] * 3
    assert rows[3].status == "pending"
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 0


def test_resume_with_backlog_post_posts_in_seq_order(client, db_session, seed, card, today):
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")], status="paused")
    first = seed.instance(netflix, 1, date(2026, 8, 22))
    second = seed.instance(netflix, 2, date(2026, 9, 22))
    db_session.commit()

    response = client.post(f"/schedules/definitions/{netflix.id}/resume", json={"backlog": "post"})

    assert response.status_code == 200
    rows = _rows(db_session, netflix)
    assert [(row.status, row.acted_by) for row in rows] == [("posted", "owner"), ("posted", "owner")]
    assert rows[0].acted_at <= rows[1].acted_at
    assert {first.id, second.id} == {row.id for row in rows}


def test_pause_and_resume_need_the_right_status(client, db_session, seed, card, today):
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")])
    db_session.commit()
    assert client.post(f"/schedules/definitions/{netflix.id}/resume").status_code == 409
    paused = client.post(f"/schedules/definitions/{netflix.id}/pause")
    assert (paused.status_code, paused.json()["status"]) == (200, "paused")
    assert client.post(f"/schedules/definitions/{netflix.id}/pause").status_code == 409
    assert client.post("/schedules/definitions/9999/pause").status_code == 404


def test_end_removes_pending_periods(client, db_session, seed, card, today):
    # Spec "End removes pending periods".
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")])
    for seq in (1, 2):
        entry = seed.entry(card, "-390", day=date(2026, 7 + seq, 22), source="schedule")
        seed.instance(netflix, seq, date(2026, 7 + seq, 22), status="posted", entries=[entry])
    for seq in range(3, 14):
        seed.instance(netflix, seq, date(2026, 10, 22) if seq == 3 else date(2027, seq - 3, 22))
    db_session.commit()

    response = client.post(f"/schedules/definitions/{netflix.id}/end")

    assert (response.status_code, response.json()["status"]) == (200, "ended")
    assert [row.status for row in _rows(db_session, netflix)] == ["posted", "posted"]


def test_ending_an_ended_definition_is_refused(client, db_session, seed, card, today):
    # Spec "Ending an ended definition".
    today(date(2026, 10, 3))
    ended = seed.definition([seed.line("expense", card, "390")], status="ended")
    db_session.commit()
    assert client.post(f"/schedules/definitions/{ended.id}/end").status_code == 409


def test_catch_up_posts_the_backlog_in_order(client, db_session, seed, card, today):
    # Spec "Catch-up posts the backlog in order".
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm")
    early = seed.instance(rent, 1, date(2026, 9, 22))
    late = seed.instance(rent, 2, date(2026, 10, 1))
    seed.instance(rent, 3, date(2026, 11, 1))
    db_session.commit()

    response = client.post(f"/schedules/definitions/{rent.id}/catch-up")

    assert response.status_code == 200
    body = response.json()
    assert (body["posted"], body["failed"], body["definition"]["posted_count"]) == ([early.id, late.id], None, 2)
    rows = _rows(db_session, rent)
    assert [(row.status, row.acted_by) for row in rows] == [("posted", "owner"), ("posted", "owner"), ("pending", None)]


def test_catch_up_refused_while_paused(client, db_session, seed, card, today):
    # Spec "Catch-up refused while paused".
    today(date(2026, 10, 3))
    paused = seed.definition([seed.line("expense", card, "390")], status="paused")
    seed.instance(paused, 1, date(2026, 9, 22))
    db_session.commit()
    assert client.post(f"/schedules/definitions/{paused.id}/catch-up").status_code == 409


def test_catch_up_stops_at_the_first_failure_and_still_answers_200(client, db_session, seed, card, today):
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm")
    broken = seed.instance(rent, 1, date(2026, 9, 22), amount_override=["0"])
    waiting = seed.instance(rent, 2, date(2026, 10, 1))
    db_session.commit()

    response = client.post(f"/schedules/definitions/{rent.id}/catch-up")

    assert response.status_code == 200
    body = response.json()
    assert body["posted"] == [] and body["failed"]["instance_id"] == broken.id
    assert body["failed"]["error"].startswith("amounts")
    rows = _rows(db_session, rent)
    assert (rows[0].status, rows[0].last_error is not None) == ("pending", True)
    assert (rows[1].id, rows[1].status, rows[1].last_error) == (waiting.id, "pending", None)


def test_catch_up_records_any_error_by_class_and_stops(client, db_session, seed, card, today, monkeypatch):
    # Spec "On any error … a separate transaction SHALL store the error": not only ValidationError.
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm")
    first = seed.instance(rent, 1, date(2026, 9, 22))
    seed.instance(rent, 2, date(2026, 10, 1))
    db_session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(posting, "post_locked", boom)
    response = client.post(f"/schedules/definitions/{rent.id}/catch-up")

    assert response.status_code == 200
    assert response.json()["failed"] == {"instance_id": first.id, "error": "RuntimeError"}
    rows = _rows(db_session, rent)
    assert [(row.status, row.last_error) for row in rows] == [("pending", "RuntimeError"), ("pending", None)]


def test_switching_to_automatic_posting(client, db_session, seed, card, today):
    # Spec "Switching to automatic posting" (the job half runs in Task 15).
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm",
                           auto_post_from=date(2026, 9, 1))
    backlog = seed.instance(rent, 1, date(2026, 9, 22))
    due = seed.instance(rent, 2, date(2026, 10, 3))
    db_session.commit()

    response = client.put(f"/schedules/definitions/{rent.id}/mode", json={"posting_mode": "auto"})

    assert (response.status_code, response.json()["auto_post_from"], response.json()["posting_mode"]) == (200, "2026-10-03", "auto")
    db_session.expire_all()
    definition = db_session.get(ScheduleDefinition, rent.id)
    rows = {row.id: row for row in _rows(db_session, rent)}
    assert posting.auto_eligible(definition, rows[due.id], date(2026, 10, 3)) is True
    assert posting.auto_eligible(definition, rows[backlog.id], date(2026, 10, 3)) is False
    assert backlog.id in [item["id"] for item in schedule_read.list_instances(db_session, queue=True)]


def test_write_during_an_import(client, db_session, seed, card, today, pg_engine):
    # Spec "Write during an import".
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")])
    db_session.commit()
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            response = client.post(f"/schedules/definitions/{netflix.id}/pause")
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    assert (response.status_code, response.json()["message"]) == (409, "import_running")


def test_state_actions_work_on_imported_definitions_before_cutover(client, db_session, seed, card, today, monkeypatch):
    # Spec "Imported definitions before cutover": pause, resume, end and mode are allowed.
    today(date(2026, 10, 3))
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "false")
    imported = seed.definition([seed.line("expense", card, "390")], created_locally=False, moze_id="P-1")
    db_session.commit()
    for call in (
        lambda: client.post(f"/schedules/definitions/{imported.id}/pause"),
        lambda: client.post(f"/schedules/definitions/{imported.id}/resume"),
        lambda: client.put(f"/schedules/definitions/{imported.id}/mode", json={"posting_mode": "confirm"}),
        lambda: client.post(f"/schedules/definitions/{imported.id}/end"),
    ):
        assert call().status_code == 200


def _flaky_post(monkeypatch, instance_id, message, times):
    """posting.post_instance raising ConflictError(message) for instance_id on its first `times` calls."""
    real = posting.post_instance
    calls = {"left": times}

    def flaky(db, target_id, **kwargs):
        if target_id == instance_id and calls["left"] > 0:
            calls["left"] -= 1
            raise ConflictError(message)
        return real(db, target_id, **kwargs)

    monkeypatch.setattr(posting, "post_instance", flaky)


@pytest.mark.parametrize("failures", [1, 2])
def test_post_sequence_retries_definition_changed_once_then_stops(client, db_session, seed, card, today, monkeypatch,
                                                                  failures):
    # R-F6: a period whose template changed under it is retried once; it never lets a later period jump the queue.
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm")
    first = seed.instance(rent, 1, date(2026, 9, 22))
    second = seed.instance(rent, 2, date(2026, 10, 1))
    db_session.commit()
    _flaky_post(monkeypatch, first.id, "definition_changed", failures)

    body = client.post(f"/schedules/definitions/{rent.id}/catch-up").json()

    rows = _rows(db_session, rent)
    if failures == 1:
        assert (body["posted"], body["failed"]) == ([first.id, second.id], None)
        assert [row.status for row in rows] == ["posted", "posted"]
    else:
        assert (body["posted"], body["failed"]) == ([], {"instance_id": first.id, "error": "ConflictError"})
        assert [(row.status, row.last_error) for row in rows] == [("pending", "ConflictError"), ("pending", None)]


def test_post_sequence_skips_already_posted_and_continues(client, db_session, seed, card, today, monkeypatch):
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm")
    first = seed.instance(rent, 1, date(2026, 9, 22))
    second = seed.instance(rent, 2, date(2026, 10, 1))
    db_session.commit()
    _flaky_post(monkeypatch, first.id, "already_posted", 1)

    body = client.post(f"/schedules/definitions/{rent.id}/catch-up").json()

    assert (body["posted"], body["failed"]) == ([second.id], None)
    assert [(row.status, row.last_error) for row in _rows(db_session, rent)] == [("pending", None), ("posted", None)]


def test_mode_change_on_ended_definition_is_refused(client, db_session, seed, card, today):
    today(date(2026, 10, 3))
    ended = seed.definition([seed.line("expense", card, "390")], status="ended", posting_mode="confirm")
    db_session.commit()

    response = client.put(f"/schedules/definitions/{ended.id}/mode", json={"posting_mode": "auto"})

    assert (response.status_code, response.json()["message"]) == (409, "definition_ended")
    db_session.expire_all()
    assert db_session.get(ScheduleDefinition, ended.id).posting_mode == "confirm"
