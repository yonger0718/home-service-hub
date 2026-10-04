"""Definition endpoints (spec "Definition endpoints", "Schedule definition model", "Imported definitions before cutover")."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import ledger_service
from app.services import schedule_generation as generation
from app.services.moze_import_service import IMPORT_LOCK_KEY


def _line(kind, account, amount, **fields) -> dict:
    return {"kind": kind, "account_id": account.id, "amount": str(amount), "currency": account.currency, **fields}


def _body(lines, **fields) -> dict:
    return {
        "kind": "recurring", "name": "Netflix", "template": {"lines": lines}, "interval_unit": "month",
        "anchor_date": "2026-10-22", **fields,
    }


def _fields(response) -> set[str]:
    return {str(error["loc"][-1]) for error in response.json()["detail"]}


def _instances(db, definition_id) -> list[ScheduleInstance]:
    db.expire_all()
    return list(
        db.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition_id).order_by(ScheduleInstance.seq))
    )


def test_local_recurring_definition(client, db_session, seed, today):
    # Spec "Local recurring definition".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()

    response = client.post("/schedules/definitions", json=_body([_line("expense", card, "390")]))

    assert response.status_code == 201
    body = response.json()
    assert (body["kind"], body["interval_unit"], body["interval_n"], body["anchor_date"], body["times"]) == (
        "recurring", "month", 1, "2026-10-22", None,
    )
    assert (body["posting_mode"], body["status"], body["auto_post_from"], body["created_locally"], body["imported"]) == (
        "auto", "active", "2026-10-03", True, False,
    )
    assert db_session.get(ScheduleDefinition, body["id"]).moze_id is None
    rows = _instances(db_session, body["id"])
    assert len(rows) == 13 and {row.status for row in rows} == {"pending"}
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 0
    assert body["template"]["lines"][0]["account_name"] == "範例卡"


def test_installment_must_be_monthly_and_have_two_periods(client, db_session, seed, today):
    # Spec "Installment must be monthly" and "Installment needs two periods".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()
    weekly = client.post(
        "/schedules/definitions", json=_body([_line("expense", card, "3333")], kind="installment", interval_unit="week", times=3)
    )
    assert weekly.status_code == 422 and _fields(weekly) == {"interval_unit"}
    once = client.post("/schedules/definitions", json=_body([_line("expense", card, "3333")], kind="installment", times=1))
    assert once.status_code == 422 and _fields(once) == {"times"}


def test_template_errors_name_the_line(client, db_session, seed, today):
    # Spec "Currency mismatch refused".
    today(date(2026, 10, 3))
    yen = seed.account("日幣現金", currency="JPY")
    db_session.commit()
    response = client.post("/schedules/definitions", json=_body([{**_line("expense", yen, "1000"), "currency": "TWD"}]))
    assert response.status_code == 422 and _fields(response) == {"lines[0].currency"}
    unknown = client.post("/schedules/definitions", json=_body([{**_line("expense", yen, "1000"), "fx_rate": "0.2"}]))
    assert unknown.status_code == 422


def test_card_installment_remainder_on_the_last_period(client, db_session, seed, today):
    # Spec "Card installment remainder on the last period".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()
    response = client.post(
        "/schedules/definitions",
        json=_body([_line("expense", card, "3333")], kind="installment", name="iPhone", times=3, total_amount="10000",
                   anchor_date="2026-10-15"),
    )
    assert response.status_code == 201
    rows = _instances(db_session, response.json()["id"])
    assert [row.due_date for row in rows] == [date(2026, 10, 15), date(2026, 11, 15), date(2026, 12, 15)]
    assert rows[-1].amount_override == ["3334"]
    too_much = client.post(
        "/schedules/definitions",
        json=_body([_line("expense", card, "5000")], kind="installment", times=3, total_amount="10000"),
    )
    assert too_much.status_code == 422 and _fields(too_much) == {"total_amount"}


def test_loan_and_schedule_in_one_call(client, db_session, seed, today):
    # Spec "Loan and schedule in one call". The scenario's 13 instances need a horizon reaching 2027-11-09, i.e.
    # today ≥ 2026-10-09 (on 2026-10-03 the horizon 2027-11-03 holds 12 periods from 2026-11-09).
    today(date(2026, 10, 9))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    db_session.commit()
    before = ledger_service.account_balance(db_session, bank.id)

    response = client.post(
        "/schedules/definitions",
        json=_body(
            [_line("repayment", bank, "8333"), _line("interest", bank, "620")],
            kind="installment", name="信貸 每月還款", times=36, anchor_date="2026-11-09",
            loan={"account_id": bank.id, "counterparty_id": lender.id, "amount": "300000", "entry_date": "2026-10-03", "name": "信貸"},
        ),
    )

    assert response.status_code == 201
    body = response.json()
    db_session.expire_all()
    assert ledger_service.account_balance(db_session, bank.id) - before == Decimal("300000")
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    assert (payable.amount, payable.source, payable.counterparty_id, payable.name) == (Decimal("300000"), "manual", lender.id, "信貸")
    assert Decimal(body["total_amount"]) == Decimal("300000")
    assert body["template"]["lines"][0]["loan_entry_id"] == payable.id == body["loan_entry_id"]
    rows = _instances(db_session, body["id"])
    assert len(rows) == 13 and {row.status for row in rows} == {"pending"}


def test_edit_regenerates_only_future_pending_periods_then_rolls_forward(client, db_session, seed, today):
    # Spec "Edit regenerates only future pending periods, then rolls forward".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "1000")], name="訂閱", anchor=date(2026, 9, 1))
    entry = seed.entry(card, "-1000", day=date(2026, 9, 1), source="schedule")
    seed.instance(definition, 1, date(2026, 9, 1), status="posted", entries=[entry])
    seed.instance(definition, 2, date(2026, 10, 1))
    generation.generate(db_session, definition, date(2026, 9, 30))  # seq 3–14 from 2026-11-01, as the scenario's GIVEN
    db_session.commit()
    assert [row.seq for row in _instances(db_session, definition.id)] == list(range(1, 15))

    response = client.put(
        f"/schedules/definitions/{definition.id}",
        json={"name": "訂閱", "template": {"lines": [_line("expense", card, "1200")]}, "interval_unit": "month",
              "anchor_date": "2026-09-01", "day_of_month": 15},
    )

    assert response.status_code == 200
    rows = _instances(db_session, definition.id)
    assert [(row.seq, row.due_date, row.status) for row in rows[:2]] == [
        (1, date(2026, 9, 1), "posted"), (2, date(2026, 10, 1), "pending"),
    ]
    assert (rows[2].seq, rows[2].due_date) == (3, date(2026, 10, 15))
    db_session.refresh(definition)
    # R-F3: the unchanged anchor is not rebased to the first regenerated period; it is only normalized to occurrence 0
    # of the new rule (day 15 → 2026-09-15), and first_seq stays 1.
    assert (definition.anchor_date, definition.first_seq, definition.day_of_month, definition.template["lines"][0]["amount"]) == (
        date(2026, 9, 15), 1, 15, "1200",
    )
    # Rolling forward one month later (horizon 2027-11-15) adds exactly the next period after 2027-10-15.
    created = generation.generate_locked(db_session, definition.id, date(2026, 10, 15))
    db_session.commit()
    last = _instances(db_session, definition.id)[-1]
    assert rows[-1].due_date == date(2027, 10, 15)
    assert (created, last.due_date, last.seq) == (1, date(2027, 11, 15), rows[-1].seq + 1)


def test_edit_keeps_the_instance_due_today_and_regenerates_from_tomorrow(client, db_session, seed, today):
    # Review Focus 5.
    today(date(2026, 10, 3))
    card, wallet = seed.account("範例卡"), seed.account("錢包")
    definition = seed.definition([seed.line("expense", card, "1000")], name="訂閱", anchor=date(2026, 9, 3))
    entry = seed.entry(card, "-1000", day=date(2026, 9, 3), source="schedule")
    seed.instance(definition, 1, date(2026, 9, 3), status="posted", entries=[entry])
    due_today = seed.instance(definition, 2, date(2026, 10, 3), amount_override=["1100"])
    generation.generate(db_session, definition, date(2026, 10, 3))
    db_session.commit()

    response = client.put(
        f"/schedules/definitions/{definition.id}",
        json={"name": "訂閱", "template": {"lines": [_line("expense", card, "1200"), _line("expense", wallet, "50")]},
              "interval_unit": "month", "anchor_date": "2026-09-03"},
    )

    assert response.status_code == 200
    rows = _instances(db_session, definition.id)
    kept = next(row for row in rows if row.id == due_today.id)
    assert (kept.seq, kept.due_date, kept.status, kept.amount_override) == (2, date(2026, 10, 3), "pending", ["1100", "50"])
    assert [(row.seq, row.due_date) for row in rows[2:4]] == [(3, date(2026, 11, 3)), (4, date(2026, 12, 3))]
    db_session.refresh(definition)
    assert (definition.anchor_date, definition.first_seq) == (date(2026, 9, 3), 1)  # R-F3: never rebased implicitly


def _month_end_definition(db_session, seed, card, **columns):
    """A monthly rule anchored on 2026-01-31: seq 1 posted, seq 2… generated on 2026-02-01 (Feb-28, Mar-31, …)."""
    definition = seed.definition([seed.line("expense", card, "1000")], name="月底", anchor=date(2026, 1, 31), **columns)
    entry = seed.entry(card, "-1000", day=date(2026, 1, 31), source="schedule")
    seed.instance(definition, 1, date(2026, 1, 31), status="posted", entries=[entry])
    generation.generate(db_session, definition, date(2026, 2, 1))
    db_session.commit()
    return definition


@pytest.mark.parametrize("day_of_month", [None, 31], ids=["implicit_day", "explicit_day"])
def test_edit_keeps_a_month_end_anchor_then_rolls_forward_on_the_31st(client, db_session, seed, today, day_of_month):
    # Multica R-F3: an edit on Feb-1 must not rebase the Jan-31 anchor to Feb-28 (which would give Mar-28 next).
    today(date(2026, 2, 1))
    card = seed.account("範例卡")
    definition = _month_end_definition(db_session, seed, card, day_of_month=day_of_month)

    response = client.put(
        f"/schedules/definitions/{definition.id}",
        json={"name": "月底", "template": {"lines": [_line("expense", card, "1200")]}, "interval_unit": "month",
              "anchor_date": "2026-01-31", "day_of_month": day_of_month},
    )

    assert response.status_code == 200
    rows = _instances(db_session, definition.id)
    assert [(row.seq, row.due_date) for row in rows[1:4]] == [
        (2, date(2026, 2, 28)), (3, date(2026, 3, 31)), (4, date(2026, 4, 30)),
    ]
    db_session.refresh(definition)
    assert (definition.anchor_date, definition.day_of_month, definition.first_seq) == (date(2026, 1, 31), day_of_month, 1)
    assert rows[-1].due_date == date(2027, 2, 28)  # horizon(2026-02-01) = 2027-03-01
    created = generation.generate_locked(db_session, definition.id, date(2026, 3, 1))  # horizon 2027-04-01
    db_session.commit()
    last = _instances(db_session, definition.id)[-1]
    assert (created, last.due_date, last.seq) == (1, date(2027, 3, 31), rows[-1].seq + 1)


def test_edit_keeps_a_leap_day_yearly_anchor_then_returns_to_feb_29(client, db_session, seed, today):
    # Multica R-F3: a Feb-29 yearly rule edited in a common year still lands on Feb-29 in the next leap year.
    today(date(2028, 3, 1))
    card = seed.account("範例卡")
    definition = seed.definition(
        [seed.line("expense", card, "990")], name="年費", interval_unit="year", anchor=date(2028, 2, 29),
        auto_post_from=date(2028, 2, 1),
    )
    entry = seed.entry(card, "-990", day=date(2028, 2, 29), source="schedule")
    seed.instance(definition, 1, date(2028, 2, 29), status="posted", entries=[entry])
    generation.generate(db_session, definition, date(2028, 3, 1))  # seq 2 on 2029-02-28
    db_session.commit()

    response = client.put(
        f"/schedules/definitions/{definition.id}",
        json={"name": "年費", "template": {"lines": [_line("expense", card, "1090")]}, "interval_unit": "year",
              "anchor_date": "2028-02-29"},
    )

    assert response.status_code == 200
    assert [(row.seq, row.due_date) for row in _instances(db_session, definition.id)] == [
        (1, date(2028, 2, 29)), (2, date(2029, 2, 28)),
    ]
    db_session.refresh(definition)
    assert (definition.anchor_date, definition.day_of_month) == (date(2028, 2, 29), None)
    generation.generate_locked(db_session, definition.id, date(2031, 3, 1))  # horizon 2032-04-01
    db_session.commit()
    assert [row.due_date for row in _instances(db_session, definition.id)][2:] == [
        date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29),
    ]


@pytest.mark.parametrize(
    ("day_of_month", "anchor", "first"),
    [(None, date(2026, 2, 10), date(2026, 2, 10)), (25, date(2026, 2, 25), date(2026, 2, 25))],
    ids=["implicit_day", "explicit_day"],
)
def test_edit_with_a_new_anchor_takes_its_day(client, db_session, seed, today, day_of_month, anchor, first):
    # Multica R-F3: only an explicit new anchor_date moves the anchor; without day_of_month its day becomes the rule's.
    today(date(2026, 2, 1))
    card = seed.account("範例卡")
    definition = _month_end_definition(db_session, seed, card)

    response = client.put(
        f"/schedules/definitions/{definition.id}",
        json={"name": "月底", "template": {"lines": [_line("expense", card, "1000")]}, "interval_unit": "month",
              "anchor_date": "2026-02-10", "day_of_month": day_of_month},
    )

    assert response.status_code == 200
    db_session.refresh(definition)
    assert (definition.anchor_date, definition.day_of_month) == (anchor, first.day)
    rows = _instances(db_session, definition.id)
    assert [(row.seq, row.due_date) for row in rows[1:3]] == [(2, first), (3, first.replace(month=3))]


def test_delete_refused_after_posting(client, db_session, seed, today):
    # Spec "Delete refused after posting".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    posted = seed.definition([seed.line("expense", card, "390")])
    entry = seed.entry(card, "-390", day=date(2026, 9, 22), source="schedule")
    seed.instance(posted, 1, date(2026, 9, 22), status="posted", entries=[entry])
    fresh = seed.definition([seed.line("expense", card, "390")], name="新")
    seed.instance(fresh, 1, date(2026, 10, 22))
    db_session.commit()
    fresh_id = fresh.id  # read before the delete: _instances() expires the session, and fresh's row is gone

    refused = client.delete(f"/schedules/definitions/{posted.id}")
    assert refused.status_code == 409 and "結束" in refused.json()["message"]
    assert client.delete(f"/schedules/definitions/{fresh_id}").status_code == 204
    assert _instances(db_session, fresh_id) == []
    assert db_session.get(ScheduleDefinition, fresh_id) is None


def test_template_edit_refused_during_the_mirror_period(client, db_session, seed, today, monkeypatch):
    # Spec "Template edit refused during the mirror period".
    today(date(2026, 10, 3))
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "false")
    bank = seed.account("薪轉")
    imported = seed.definition(
        [seed.line("expense", bank, "8333")], kind="installment", name="信貸", times=36, created_locally=False, moze_id="I-1"
    )
    db_session.commit()
    body = {"name": "信貸", "template": {"lines": [_line("expense", bank, "9000")]}, "interval_unit": "month",
            "anchor_date": "2026-10-22", "times": 36}
    for response in (client.put(f"/schedules/definitions/{imported.id}", json=body),
                     client.delete(f"/schedules/definitions/{imported.id}")):
        assert (response.status_code, response.json()["message"]) == (409, "locked_until_cutover")
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert client.put(f"/schedules/definitions/{imported.id}", json=body).status_code == 200


def test_writes_are_refused_while_an_import_runs(client, db_session, seed, today, pg_engine):
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            response = client.post("/schedules/definitions", json=_body([_line("expense", card, "390")]))
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    assert (response.status_code, response.json()["message"]) == (409, "import_running")


def test_get_definition_lists_its_instances_and_unknown_ids_are_404(client, db_session, seed, today):
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()
    created = client.post("/schedules/definitions", json=_body([_line("expense", card, "390")])).json()
    detail = client.get(f"/schedules/definitions/{created['id']}").json()
    assert [item["seq"] for item in detail["instances"]] == list(range(1, 14))
    assert client.get("/schedules/definitions").json()[0]["id"] == created["id"]
    for response in (client.get("/schedules/definitions/9999"), client.delete("/schedules/definitions/9999")):
        assert response.status_code == 404


def test_put_without_posting_mode_keeps_the_stored_mode(client, db_session, seed, today):
    # DefinitionUpdateIn.posting_mode = None keeps the mode: a PUT never switches 提醒入帳 to 自動入帳 silently.
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    rent = seed.definition(
        [seed.line("expense", card, "18000")], name="房租", posting_mode="confirm", anchor=date(2026, 10, 25),
        auto_post_from=date(2026, 9, 1),
    )
    db_session.commit()

    response = client.put(
        f"/schedules/definitions/{rent.id}",
        json={"name": "房租", "template": {"lines": [_line("expense", card, "18500")]}, "interval_unit": "month",
              "anchor_date": "2026-10-25"},
    )

    assert response.status_code == 200
    assert (response.json()["posting_mode"], response.json()["auto_post_from"]) == ("confirm", "2026-09-01")


def test_day_of_month_before_the_start_day_starts_next_month(client, db_session, seed, today):
    # Spec "anchor_date … occurrence 0 of the rule": 起始日 09-20 with day 15 stores 10-15 as the anchor.
    today(date(2026, 9, 18))
    card = seed.account("範例卡")
    db_session.commit()

    response = client.post(
        "/schedules/definitions", json=_body([_line("expense", card, "390")], anchor_date="2026-09-20", day_of_month=15)
    )

    assert response.status_code == 201
    assert response.json()["anchor_date"] == "2026-10-15"
    assert _instances(db_session, response.json()["id"])[0].due_date == date(2026, 10, 15)


def test_schedule_writes_need_the_bearer_token_when_api_tokens_are_set(client, db_session, seed, today, monkeypatch):
    # #42: ApiTokenMiddleware covers the new routes with no extra code (design D42).
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()
    monkeypatch.setenv("ACCOUNTING_API_TOKENS", "test:synthetic-token-1")

    refused = client.post("/schedules/definitions", json=_body([_line("expense", card, "390")]))
    allowed = client.post(
        "/schedules/definitions", json=_body([_line("expense", card, "390")]),
        headers={"Authorization": "Bearer synthetic-token-1"},
    )

    assert refused.status_code == 401
    assert allowed.status_code == 201
