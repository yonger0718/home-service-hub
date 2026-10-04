"""Instance endpoints (spec "Instance endpoints", "Imported definitions before cutover")."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import schedule_generation as generation
from app.services import schedule_posting as posting
from app.services import settlement_service
from app.services.moze_import_service import IMPORT_LOCK_KEY


def _fields(response) -> set[str]:
    return {str(error["loc"][-1]) for error in response.json()["detail"]}


def _row(db, instance_id) -> ScheduleInstance:
    db.expire_all()
    return db.get(ScheduleInstance, instance_id)


def _open(db, entry_id) -> Decimal:
    db.expire_all()
    return settlement_service.open_amount(db, db.get(LedgerEntry, entry_id))


@pytest.fixture()
def loan(seed):
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, name="信貸", day=date(2026, 10, 3))
    definition = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸 每月還款", anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    return SimpleNamespace(bank=bank, lender=lender, payable=payable, definition=definition)


def test_queue_endpoint(client, db_session, seed, today):
    # Spec "待完成交易 queue" through HTTP.
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm", times=12)
    overdue = seed.instance(rent, 4, date(2026, 9, 30))
    later = seed.instance(rent, 5, date(2026, 10, 20))
    db_session.commit()
    items = client.get("/schedules/instances", params={"queue": "true"}).json()
    assert [(item["id"], item["overdue_days"]) for item in items] == [(overdue.id, 3), (later.id, 0)]
    assert Decimal(items[0]["lines"][0]["amount"]) == Decimal("-18000")
    assert client.get("/schedules/instances", params={"status": "all", "until": "2026-12-31"}).status_code == 200


def test_override_length_checked(client, db_session, seed, loan, today):
    # Spec "Override length checked".
    today(date(2026, 10, 3))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    db_session.commit()
    response = client.put(f"/schedules/instances/{instance.id}", json={"amounts": ["8333"]})
    assert response.status_code == 422 and _fields(response) == {"amounts"}


def test_instance_edit_moves_the_date_and_marks_the_owner(client, db_session, seed, loan, today):
    today(date(2026, 10, 3))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    db_session.commit()
    response = client.put(f"/schedules/instances/{instance.id}", json={"due_date": "2026-11-12", "amounts": ["9000", "600"]})
    assert response.status_code == 200
    row = _row(db_session, instance.id)
    assert (row.due_date, row.rule_date, row.amount_override, row.edited_by_owner) == (
        date(2026, 11, 12), date(2026, 11, 9), ["9000", "600"], True,
    )


def test_date_edit_onto_a_posted_day_refused(client, db_session, seed, today):
    # Spec "Date edit onto a posted day refused".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    weekly = seed.definition([seed.line("expense", card, "100")], interval_unit="week", anchor=date(2026, 10, 5))
    entry = seed.entry(card, "-100", day=date(2026, 10, 5), source="schedule")
    seed.instance(weekly, 1, date(2026, 10, 5), status="posted", entries=[entry])
    pending = seed.instance(weekly, 2, date(2026, 10, 12))
    db_session.commit()
    response = client.put(f"/schedules/instances/{pending.id}", json={"due_date": "2026-10-05"})
    assert response.status_code == 422 and _fields(response) == {"due_date"}


def test_post_endpoint_posts_and_a_second_post_is_409(client, db_session, seed, loan, today):
    # Spec "Posting twice is refused" through HTTP.
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    db_session.commit()
    first = client.post(f"/schedules/instances/{instance.id}/post")
    assert (first.status_code, first.json()["status"], first.json()["acted_by"]) == (200, "posted", "owner")
    count = db_session.scalar(select(func.count()).select_from(LedgerEntry))
    second = client.post(f"/schedules/instances/{instance.id}/post")
    assert (second.status_code, second.json()["message"]) == (409, "already_posted")
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == count


def test_skip_leaves_the_loan_open(client, db_session, seed, loan, today):
    # Spec "Skip leaves the loan open".
    today(date(2026, 12, 1))
    seed.entry(loan.bank, "-8333", kind="payable", counterparty_id=loan.lender.id, settles_entry_id=loan.payable.id,
               is_settlement=True)
    instance = seed.instance(loan.definition, 2, date(2026, 12, 9))
    db_session.commit()
    assert _open(db_session, loan.payable.id) == Decimal("291667")
    response = client.post(f"/schedules/instances/{instance.id}/skip")
    assert (response.status_code, response.json()["status"], response.json()["acted_by"]) == (200, "skipped", "owner")
    assert _open(db_session, loan.payable.id) == Decimal("291667")
    assert client.post(f"/schedules/instances/{instance.id}/skip").status_code == 409


def test_reopen_revives_an_ended_definition(client, db_session, seed, today):
    # Spec "Reopen revives an ended definition".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    ended = seed.definition([seed.line("expense", card, "390")], status="ended", times=1)
    last = seed.instance(ended, 1, date(2026, 9, 22), status="skipped", acted_by="owner")
    db_session.commit()
    response = client.post(f"/schedules/instances/{last.id}/reopen")
    assert (response.status_code, response.json()["status"], response.json()["reopened"]) == (200, "pending", True)
    db_session.expire_all()
    assert db_session.get(ScheduleDefinition, ended.id).status == "active"
    assert client.post(f"/schedules/instances/{last.id}/reopen").status_code == 409


def test_archived_account_found_at_posting_time(client, db_session, seed, today):
    # Spec "Archived account found at posting time".
    today(date(2026, 10, 3))
    card, old = seed.account("範例卡"), seed.account("舊帳戶")
    ended = seed.definition([seed.line("expense", card, "390"), seed.line("expense", old, "20")], status="ended")
    skipped = seed.instance(ended, 1, date(2026, 9, 22), status="skipped", acted_by="owner")
    old.is_archived = True
    db_session.commit()

    assert client.post(f"/schedules/instances/{skipped.id}/reopen").status_code == 200
    posted = client.post(f"/schedules/instances/{skipped.id}/post")

    assert posted.status_code == 422 and _fields(posted) == {"lines[1].account_id"}
    row = _row(db_session, skipped.id)
    assert row.status == "pending" and row.last_error.startswith("lines[1].account_id")
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.source == "schedule")) == 0


def test_reopen_of_a_posted_period_deletes_its_entries(client, db_session, seed, loan, today):
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    db_session.commit()
    client.post(f"/schedules/instances/{instance.id}/post")
    response = client.post(f"/schedules/instances/{instance.id}/reopen")
    assert (response.status_code, response.json()["status"], response.json()["note"]) == (
        200, "pending", "入帳記錄已於 2026-11-09 刪除",
    )
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.source == "schedule")) == 0
    assert _open(db_session, loan.payable.id) == Decimal("300000")


def test_repost_a_repayment_with_a_corrected_amount(client, db_session, seed, loan, today):
    # Spec "Repost a repayment with a corrected amount".
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    result = posting.post_instance(db_session, instance.id, actor="auto")
    db_session.commit()

    response = client.post(f"/schedules/instances/{instance.id}/repost", json={"amounts": ["8333", "598"]})

    assert (response.status_code, response.json()["status"]) == (200, "posted")
    db_session.expire_all()
    assert all(db_session.get(LedgerEntry, entry_id) is None for entry_id in result.entry_ids)
    new_ids = response.json()["posted_entry_ids"]
    assert sorted(db_session.get(LedgerEntry, entry_id).amount for entry_id in new_ids) == [Decimal("-8333"), Decimal("-598")]
    assert response.json()["edited_by_owner"] is True
    other = seed.instance(loan.definition, 2, date(2026, 12, 9))
    db_session.commit()
    assert client.post(f"/schedules/instances/{other.id}/repost", json={"amounts": ["1", "1"]}).status_code == 409


def test_repost_of_a_moze_booked_period_refused_before_cutover(client, db_session, seed, today, monkeypatch):
    # Spec "Repost of a MOZE-booked period refused before cutover".
    today(date(2026, 10, 3))
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "false")
    card = seed.account("範例卡")
    imported = seed.definition([seed.line("expense", card, "390")], created_locally=False, moze_id="P-1")
    entry = seed.entry(card, "-390", day=date(2026, 9, 22), source="moze_backup", moze_id="R-9")
    booked = seed.instance(imported, 1, date(2026, 9, 22), status="posted", entries=[entry], acted_by="import", moze_id="R-9")
    db_session.commit()
    response = client.post(f"/schedules/instances/{booked.id}/repost", json={"amounts": ["400"]})
    assert (response.status_code, response.json()["message"]) == (409, "locked_until_cutover")


def test_accept_a_partial_period(client, db_session, seed, loan, today):
    # Spec "Accept a partial period".
    today(date(2026, 11, 10))
    repayment = seed.entry(loan.bank, "-8333", kind="payable", counterparty_id=loan.lender.id,
                           settles_entry_id=loan.payable.id, is_settlement=True, day=date(2026, 11, 9), source="schedule")
    partial = seed.instance(loan.definition, 1, date(2026, 11, 9), status="posted", entries=[repayment],
                            is_partial=True, note="部分入帳記錄已於 2026-11-10 刪除")
    db_session.commit()
    assert partial.id in [item["id"] for item in client.get("/schedules/instances", params={"queue": "true"}).json()]

    response = client.post(f"/schedules/instances/{partial.id}/accept-partial")

    assert (response.status_code, response.json()["is_partial"], response.json()["note"]) == (
        200, False, "部分入帳記錄已於 2026-11-10 刪除",
    )
    assert db_session.get(LedgerEntry, repayment.id) is not None
    assert partial.id not in [item["id"] for item in client.get("/schedules/instances", params={"queue": "true"}).json()]
    assert client.post(f"/schedules/instances/{partial.id}/accept-partial").status_code == 409


def test_instance_writes_are_refused_while_an_import_runs(client, db_session, seed, loan, today, pg_engine):
    today(date(2026, 10, 3))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    db_session.commit()
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            responses = [client.post(f"/schedules/instances/{instance.id}/{action}") for action in ("skip", "post")]
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    assert [(response.status_code, response.json()["message"]) for response in responses] == [(409, "import_running")] * 2
    assert _row(db_session, instance.id).status == "pending"


def test_date_edit_onto_another_pending_period_refused(client, db_session, seed, today):
    # Two pending periods on one day would collide on ux_schedule_instance_posted_day when the second posts.
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    weekly = seed.definition([seed.line("expense", card, "100")], interval_unit="week", anchor=date(2026, 10, 5))
    seed.instance(weekly, 1, date(2026, 10, 5))
    second = seed.instance(weekly, 2, date(2026, 10, 12))
    db_session.commit()
    response = client.put(f"/schedules/instances/{second.id}", json={"due_date": "2026-10-05"})
    assert response.status_code == 422 and _fields(response) == {"due_date"}
    assert _row(db_session, second.id).due_date == date(2026, 10, 12)


def test_post_records_any_error_and_answers_409(client, db_session, seed, today, monkeypatch):
    # Spec "On any error … a separate transaction SHALL store the error": a non-validation error is stored by class.
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    netflix = seed.definition([seed.line("expense", card, "390")])
    instance = seed.instance(netflix, 1, date(2026, 10, 3))
    db_session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(posting, "post_locked", boom)
    response = client.post(f"/schedules/instances/{instance.id}/post")

    assert (response.status_code, response.json()["message"]) == (409, "RuntimeError")
    row = _row(db_session, instance.id)
    assert (row.status, row.last_error) == ("pending", "RuntimeError")


# --- Amount edit scope (spec "Instance amount edit scope", proposal decision 24) ---


@pytest.fixture()
def netflix(seed):
    """Monthly 390: seq 1 posted, seq 2–4 pending without overrides."""
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 9, 22))
    entry = seed.entry(card, "-390", day=date(2026, 9, 22), source="schedule")
    first = seed.instance(definition, 1, date(2026, 9, 22), status="posted", entries=[entry])
    rows = [seed.instance(definition, seq, day) for seq, day in (
        (2, date(2026, 10, 22)), (3, date(2026, 11, 22)), (4, date(2026, 12, 22)),
    )]
    return SimpleNamespace(card=card, definition=definition, first=first, rows=rows)


def _template(db, definition_id) -> tuple[list[str], bool]:
    db.expire_all()
    definition = db.get(ScheduleDefinition, definition_id)
    return [line["amount"] for line in definition.template["lines"]], definition.template_owner_edited


def _overrides(db, rows) -> list[tuple]:
    return [(_row(db, row.id).amount_override, _row(db, row.id).edited_by_owner) for row in rows]


def test_only_this_period_by_default(client, db_session, netflix, today):
    # Spec "Only this period by default".
    today(date(2026, 10, 3))
    db_session.commit()
    response = client.put(f"/schedules/instances/{netflix.rows[1].id}", json={"amounts": ["420"]})
    assert response.status_code == 200
    assert _overrides(db_session, netflix.rows) == [(None, False), (["420"], True), (None, False)]
    assert _template(db_session, netflix.definition.id) == (["390"], False)


def test_this_period_and_the_following_ones_keep_the_old_price_before(client, db_session, netflix, today):
    # Spec "This period and the following ones keep the old price before".
    today(date(2026, 10, 3))
    db_session.commit()
    response = client.put(
        f"/schedules/instances/{netflix.rows[1].id}", json={"amounts": ["420"], "scope": "following"}
    )
    assert (response.status_code, response.json()["amounts"]) == (200, ["420"])
    assert _overrides(db_session, netflix.rows) == [(["390"], False), (None, False), (None, False)]
    assert _template(db_session, netflix.definition.id) == (["420"], True)
    first = _row(db_session, netflix.first.id)
    assert (first.status, first.amount_override) == ("posted", ["390"])  # its posted amount, pinned (metadata only)
    detail = client.get(f"/schedules/definitions/{netflix.definition.id}").json()
    assert detail["template_owner_edited"] is True
    pending = [item["amounts"] for item in detail["instances"] if item["status"] == "pending"]
    assert pending == [["390"], ["420"], ["420"]]  # seq 2 keeps the old price; seq 3 and 4 follow the template


def test_following_replaces_the_edited_periods_own_override_and_keeps_later_owner_edits(
    client, db_session, netflix, today
):
    today(date(2026, 10, 3))
    netflix.rows[1].amount_override, netflix.rows[1].edited_by_owner = ["405"], True
    netflix.rows[2].amount_override, netflix.rows[2].edited_by_owner = ["400"], True
    db_session.commit()
    response = client.put(
        f"/schedules/instances/{netflix.rows[1].id}", json={"amounts": ["420"], "scope": "following"}
    )
    assert response.status_code == 200
    assert _overrides(db_session, netflix.rows) == [(["390"], False), (["420"], True), (["400"], True)]


def test_all_periods_keep_other_owner_edits(client, db_session, netflix, today):
    # Spec "All periods keep other owner edits".
    today(date(2026, 10, 3))
    netflix.rows[2].amount_override, netflix.rows[2].edited_by_owner = ["400"], True
    db_session.commit()
    response = client.put(f"/schedules/instances/{netflix.rows[0].id}", json={"amounts": ["420"], "scope": "all"})
    assert response.status_code == 200
    assert _overrides(db_session, netflix.rows) == [(None, False), (None, False), (["400"], True)]
    assert _template(db_session, netflix.definition.id) == (["420"], True)
    assert _row(db_session, netflix.first.id).status == "posted"


def test_a_scope_with_another_field_is_refused(client, db_session, netflix, today):
    # Spec "A scope with another field is refused"; amounts become template amounts, so "0" is refused too.
    today(date(2026, 10, 3))
    db_session.commit()
    target = netflix.rows[0].id
    for body in (
        {"amounts": ["420"], "due_date": "2026-10-23", "scope": "following"},
        {"due_date": "2026-10-23", "scope": "all"},
    ):
        response = client.put(f"/schedules/instances/{target}", json=body)
        assert response.status_code == 422 and _fields(response) == {"scope"}
    zero = client.put(f"/schedules/instances/{target}", json={"amounts": ["0"], "scope": "all"})
    assert zero.status_code == 422 and _fields(zero) == {"amounts"}
    posted = client.put(f"/schedules/instances/{netflix.first.id}", json={"amounts": ["420"], "scope": "all"})
    assert posted.status_code == 409
    assert _template(db_session, netflix.definition.id) == (["390"], False)
    row = _row(db_session, target)
    assert (row.due_date, row.amount_override, row.edited_by_owner) == (date(2026, 10, 22), None, False)


def test_scope_edit_allowed_on_an_imported_definition_before_cutover(client, db_session, seed, today, monkeypatch):
    # Spec "Imported definition before cutover": amounts are not rule fields.
    today(date(2026, 10, 3))
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "false")
    card = seed.account("範例卡")
    imported = seed.definition([seed.line("expense", card, "390")], created_locally=False, moze_id="P-1")
    pending = seed.instance(imported, 1, date(2026, 10, 22), moze_id="R-1", amount_override=["390"])
    db_session.commit()
    response = client.put(f"/schedules/instances/{pending.id}", json={"amounts": ["420"], "scope": "all"})
    assert response.status_code == 200
    assert _template(db_session, imported.id) == (["420"], True)
    assert _row(db_session, pending.id).amount_override is None
    refused = client.delete(f"/schedules/definitions/{imported.id}")
    assert (refused.status_code, refused.json()["message"]) == (409, "locked_until_cutover")


def test_installment_scope_keeps_the_last_period_remainder(client, db_session, seed, loan, today):
    today(date(2026, 10, 3))
    before_last = seed.instance(loan.definition, 35, date(2029, 9, 9))
    last = seed.instance(loan.definition, 36, date(2029, 10, 9), amount_override=["8345", "620"])
    db_session.commit()
    too_much = client.put(
        f"/schedules/instances/{before_last.id}", json={"amounts": ["9000", "598"], "scope": "following"}
    )
    assert too_much.status_code == 422 and _fields(too_much) == {"amounts"}  # 9000 × 35 ≥ 300000
    response = client.put(
        f"/schedules/instances/{before_last.id}", json={"amounts": ["8333", "598"], "scope": "following"}
    )
    assert response.status_code == 200
    assert _template(db_session, loan.definition.id) == (["8333", "598"], True)
    assert _row(db_session, before_last.id).amount_override is None
    assert _row(db_session, last.id).amount_override == ["8345", "598"]  # remainder of 300000 − 35 × 8333, new interest


def _card_installment(seed, times, amount, total, *, posted=()):
    """A local expense installment; seqs in `posted` are posted at the template amount, the others pending."""
    card = seed.account("範例卡")
    definition = seed.definition(
        [seed.line("expense", card, amount)], kind="installment", name="分期", anchor=date(2026, 9, 15), times=times,
        total_amount=Decimal(total),
    )
    rows = []
    for seq in range(1, times + 1):
        day = date(2026, 8 + seq, 15) if 8 + seq <= 12 else date(2027, 8 + seq - 12, 15)
        if seq in posted:
            entry = seed.entry(card, f"-{amount}", day=day, source="schedule")
            rows.append(seed.instance(definition, seq, day, status="posted", entries=[entry]))
        else:
            rows.append(seed.instance(definition, seq, day))
    return SimpleNamespace(definition=definition, rows=rows)


def _first_amounts(db, rows) -> list[str]:
    db.expire_all()
    definition = db.get(ScheduleDefinition, rows[0].definition_id)
    template = definition.template["lines"][0]["amount"]
    return [
        (_row(db, row.id).amount_override or [template])[0] for row in rows
    ]


def test_installment_following_keeps_the_total_against_the_actual_allocations(client, db_session, seed, today):
    # Multica R-A1: 10,000 over 3 (3,333 / 3,333 / 3,334); 這一期與之後 from the second to 3,000 must give
    # 3,333 / 3,000 / 3,667 = 10,000 — never 3,333 / 3,000 / 4,000.
    today(date(2026, 9, 1))
    plan = _card_installment(seed, 3, "3333", "10000")
    plan.rows[2].amount_override = ["3334"]
    db_session.commit()
    response = client.put(f"/schedules/instances/{plan.rows[1].id}", json={"amounts": ["3000"], "scope": "following"})
    assert response.status_code == 200
    amounts = _first_amounts(db_session, plan.rows)
    assert amounts == ["3333", "3000", "3667"] and sum(Decimal(value) for value in amounts) == Decimal("10000")


def test_installment_scope_counts_posted_and_owner_edited_periods(client, db_session, seed, today):
    # Multica R-A1: a principal change after a posted and an owner-edited period; 全部週期 from the third.
    today(date(2026, 10, 1))
    plan = _card_installment(seed, 4, "2500", "10000", posted={1})
    plan.rows[1].amount_override, plan.rows[1].edited_by_owner = ["2000"], True
    db_session.commit()
    response = client.put(f"/schedules/instances/{plan.rows[2].id}", json={"amounts": ["2600"], "scope": "all"})
    assert response.status_code == 200
    # seq 1 posted at 2,500 (its amount now pinned); 2,500 + 2,000 + 2,600 + 2,900 = 10,000
    assert [_row(db_session, row.id).amount_override for row in plan.rows] == [["2500"], ["2000"], None, ["2900"]]
    assert _template(db_session, plan.definition.id) == (["2600"], True)


def test_installment_scope_that_would_overbook_is_refused(client, db_session, seed, today):
    # Multica R-A1: the old per-period check (4,000 × 2 < 10,000) passes, but the posted 6,000 makes Σ reach the total.
    today(date(2026, 10, 1))
    plan = _card_installment(seed, 3, "3333", "10000", posted={1})
    plan.rows[0].amount_override = ["6000"]
    db_session.commit()
    response = client.put(f"/schedules/instances/{plan.rows[1].id}", json={"amounts": ["4000"], "scope": "following"})
    assert response.status_code == 422 and _fields(response) == {"amounts"}
    assert "4000" not in response.text and "6000" not in response.text
    assert _template(db_session, plan.definition.id) == (["3333"], False)


def test_generation_after_scoped_edit_keeps_the_total(client, db_session, seed, today):
    # Controller ruling on R-A1: a last period generated AFTER a scoped edit takes the residual against the actual
    # allocations (2,500 + 2,600 + 2,600 + 2,300 = 10,000), not total − amount × (times − 1) (2,200, Σ 9,900).
    today(date(2026, 10, 1))
    card = seed.account("範例卡")
    definition = seed.definition(
        [seed.line("expense", card, "2500")], kind="installment", name="分期", anchor=date(2026, 9, 15), times=4,
        total_amount=Decimal("10000"),
    )
    rows = [seed.instance(definition, seq, date(2026, 8 + seq, 15)) for seq in (1, 2, 3)]
    db_session.commit()
    response = client.put(f"/schedules/instances/{rows[1].id}", json={"amounts": ["2600"], "scope": "following"})
    assert response.status_code == 200

    assert generation.generate_locked(db_session, definition.id, date(2026, 10, 1)) == 1
    db_session.commit()

    last = db_session.scalar(
        select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id, ScheduleInstance.seq == 4)
    )
    assert last.amount_override == ["2300"]
    rows.append(last)
    amounts = _first_amounts(db_session, rows)
    assert amounts == ["2500", "2600", "2600", "2300"] and sum(Decimal(value) for value in amounts) == Decimal("10000")


def test_repeated_following_edits_keep_the_total(client, db_session, seed, today):
    # Review of R-A1: a posted period keeps the amount it was posted with across successive template changes, so the
    # last period generated later closes the total (2,500 + 2,600 + 2,700 + 2,700 + 2,000 = 12,500).
    today(date(2026, 10, 1))
    card = seed.account("範例卡")
    definition = seed.definition(
        [seed.line("expense", card, "2500")], kind="installment", name="分期", anchor=date(2026, 9, 15), times=5,
        total_amount=Decimal("12500"),
    )
    entry = seed.entry(card, "-2500", day=date(2026, 9, 15), source="schedule")
    rows = [seed.instance(definition, 1, date(2026, 9, 15), status="posted", entries=[entry])]
    rows += [seed.instance(definition, seq, date(2026, 8 + seq, 15)) for seq in (2, 3, 4)]
    db_session.commit()

    first = client.put(f"/schedules/instances/{rows[1].id}", json={"amounts": ["2600"], "scope": "following"})
    assert first.status_code == 200
    assert _row(db_session, rows[0].id).amount_override == ["2500"]  # materialised before the template changed
    today(date(2026, 10, 15))
    assert client.post(f"/schedules/instances/{rows[1].id}/post").status_code == 200
    second = client.put(f"/schedules/instances/{rows[2].id}", json={"amounts": ["2700"], "scope": "following"})
    assert second.status_code == 200
    assert generation.generate_locked(db_session, definition.id, date(2026, 10, 15)) == 1
    db_session.commit()

    last = db_session.scalar(
        select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id, ScheduleInstance.seq == 5)
    )
    rows.append(last)
    assert [_row(db_session, row.id).amount_override for row in rows[:2]] == [["2500"], ["2600"]]
    amounts = _first_amounts(db_session, rows)
    assert amounts == ["2500", "2600", "2700", "2700", "2000"]
    assert sum(Decimal(value) for value in amounts) == Decimal("12500")
    assert _row(db_session, rows[0].id).posted_entry_ids == [entry.id]  # the posted row's entries are untouched


def test_reopen_clears_a_misaligned_override(client, db_session, seed, today):
    # A posted row's amount_override carries the amounts it was posted with (pinned before a template change). Back
    # to pending, an override whose line count no longer matches the template is cleared; a matching one is kept.
    today(date(2026, 10, 23))
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "390")], name="串流")
    entry = seed.entry(card, "-390", day=date(2026, 9, 22), source="schedule")
    pinned = seed.instance(definition, 1, date(2026, 9, 22), status="posted", entries=[entry],
                           amount_override=["390", "149"])
    skipped = seed.instance(definition, 2, date(2026, 10, 22), status="skipped", acted_by="owner",
                            amount_override=["400"])
    db_session.commit()

    assert client.post(f"/schedules/instances/{pinned.id}/reopen").status_code == 200
    assert client.post(f"/schedules/instances/{skipped.id}/reopen").status_code == 200

    assert (_row(db_session, pinned.id).status, _row(db_session, pinned.id).amount_override) == ("pending", None)
    assert (_row(db_session, skipped.id).status, _row(db_session, skipped.id).amount_override) == ("pending", ["400"])
