"""The ledger's side of schedules (spec "Deleting entries of a posted period", "Rows referenced by definitions",
ledger "Schedule-sourced entries")."""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import schedule_posting as posting
from app.services import schedule_read, settlement_service
from app.services.moze_import_service import IMPORT_LOCK_KEY


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


def _posted_period(db, seed, loan, today) -> tuple[ScheduleInstance, int, int]:
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    result = posting.post_instance(db, instance.id, actor="auto")
    db.commit()
    repayment_id, interest_id = result.entry_ids
    return instance, repayment_id, interest_id


def _row(db, instance_id) -> ScheduleInstance:
    db.expire_all()
    return db.get(ScheduleInstance, instance_id)


def test_deleting_the_interest_leaves_a_partial_period(client, db_session, seed, loan, today):
    # Spec "Deleting the interest leaves a partial period".
    instance, repayment_id, interest_id = _posted_period(db_session, seed, loan, today)
    today(date(2026, 11, 10))

    assert client.delete(f"/entries/{interest_id}").status_code == 204

    row = _row(db_session, instance.id)
    assert (row.status, row.is_partial, row.posted_entry_ids, row.note) == (
        "posted", True, [repayment_id], "部分入帳記錄已於 2026-11-10 刪除",
    )
    assert db_session.get(LedgerEntry, repayment_id) is not None
    assert instance.id in [item["id"] for item in schedule_read.list_instances(db_session, queue=True)]


def test_deleting_the_last_entry_reopens_the_period(client, db_session, seed, loan, today):
    # Spec "Deleting the last entry reopens the period".
    instance, repayment_id, interest_id = _posted_period(db_session, seed, loan, today)
    today(date(2026, 11, 10))
    client.delete(f"/entries/{interest_id}")

    assert client.delete(f"/entries/{repayment_id}").status_code == 204

    row = _row(db_session, instance.id)
    assert (row.status, row.posted_entry_ids, row.acted_at, row.acted_by, row.is_partial) == ("pending", [], None, None, False)
    assert row.reopened_at is not None and row.note == "入帳記錄已於 2026-11-10 刪除"
    assert settlement_service.open_amount(db_session, db_session.get(LedgerEntry, loan.payable.id)) == Decimal("300000")
    definition = db_session.get(ScheduleDefinition, loan.definition.id)
    assert posting.auto_eligible(definition, row, date(2026, 11, 10)) is False


def test_deleting_a_moze_booked_period(client, db_session, seed, today, monkeypatch):
    # Spec "Deleting a MOZE-booked period".
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    today(date(2026, 10, 6))
    pay, broker = seed.account("薪轉"), seed.account("交割")
    pair = uuid.uuid4()
    out_leg = seed.entry(pay, "-15000", kind="transfer_out", transfer_group_id=pair, source="moze_backup", moze_id="R-OUT",
                         day=date(2026, 10, 5))
    in_leg = seed.entry(broker, "15000", kind="transfer_in", transfer_group_id=pair, source="moze_backup", moze_id="R-IN",
                        day=date(2026, 10, 5))
    definition = seed.definition([seed.line("transfer", pay, "15000", to_account_id=broker.id)], created_locally=False,
                                 moze_id="P-1", anchor=date(2026, 10, 5))
    booked = seed.instance(definition, 1, date(2026, 10, 5), status="posted", entries=[out_leg, in_leg], acted_by="import",
                           moze_id="R-OUT")
    db_session.commit()

    assert client.delete(f"/entries/{in_leg.id}").status_code == 204

    row = _row(db_session, booked.id)
    assert (row.status, row.note, row.posted_entry_ids) == ("skipped", "入帳記錄已刪除", [])
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 0


def test_deleting_from_an_ended_definition_revives_it(client, db_session, seed, today):
    # Spec "Deleting from an ended definition revives it".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    ended = seed.definition([seed.line("expense", card, "390")], status="ended", times=1)
    expense = seed.entry(card, "-390", day=date(2026, 9, 22), source="schedule")
    last = seed.instance(ended, 1, date(2026, 9, 22), status="posted", entries=[expense])
    db_session.commit()

    assert client.delete(f"/entries/{expense.id}").status_code == 204

    assert _row(db_session, last.id).status == "pending"
    assert db_session.get(ScheduleDefinition, ended.id).status == "active"


def test_deleting_one_leg_of_a_scheduled_transfer(client, db_session, seed, today):
    # Ledger spec "Deleting one leg of a scheduled transfer".
    today(date(2026, 10, 5))
    pay, broker = seed.account("薪轉"), seed.account("交割")
    definition = seed.definition([seed.line("transfer", pay, "15000", to_account_id=broker.id)], anchor=date(2026, 10, 5))
    instance = seed.instance(definition, 1, date(2026, 10, 5))
    out_id, in_id = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()

    assert client.delete(f"/entries/{in_id}").status_code == 204

    row = _row(db_session, instance.id)
    assert (row.status, row.reopened_at is not None) == ("pending", True)
    assert db_session.get(LedgerEntry, out_id) is None and db_session.get(LedgerEntry, in_id) is None


def test_archiving_a_paying_account_is_refused(client, db_session, seed, loan, today):
    # Spec "Archiving a paying account".
    today(date(2026, 10, 3))
    db_session.commit()
    archive = client.put(f"/accounts/{loan.bank.id}", json={"name": "薪轉", "currency": "TWD", "is_archived": True})
    assert archive.status_code == 409 and "信貸 每月還款" in archive.json()["message"]
    remove = client.delete(f"/accounts/{loan.bank.id}")
    assert remove.status_code == 409 and "信貸 每月還款" in remove.json()["message"]


def test_deleting_a_loan_with_an_active_schedule_is_refused(client, db_session, seed, loan, today):
    # Spec "Deleting a loan with an active schedule".
    today(date(2026, 10, 3))
    db_session.commit()
    response = client.delete(f"/entries/{loan.payable.id}")
    assert response.status_code == 409 and "信貸 每月還款" in response.json()["message"]


def test_categories_projects_and_counterparties_are_protected_until_the_schedule_ends(client, db_session, seed, today):
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    streaming = seed.category("串流")
    life = seed.project("生活")
    alan = seed.counterparty("Alan")
    netflix = seed.definition([seed.line("expense", card, "390", category_id=streaming.id, project_id=life.id)])
    lend = seed.definition([seed.line("receivable", card, "500", counterparty_id=alan.id)], name="代墊")
    db_session.commit()

    for response in (
        client.put(f"/categories/{streaming.id}", json={"kind": "expense", "name": "串流", "is_hidden": True}),
        client.delete(f"/categories/{streaming.id}"),
        client.put(f"/projects/{life.id}", json={"name": "生活", "is_archived": True}),
        client.delete(f"/projects/{life.id}"),
        client.delete(f"/counterparties/{alan.id}"),
    ):
        assert response.status_code == 409

    client.post(f"/schedules/definitions/{netflix.id}/end")
    client.post(f"/schedules/definitions/{lend.id}/end")
    assert client.put(f"/categories/{streaming.id}", json={"kind": "expense", "name": "串流", "is_hidden": True}).status_code == 200
    assert client.delete(f"/counterparties/{alan.id}").status_code == 204


def test_split_edit_on_a_scheduled_installment_group_refused(client, db_session, seed, loan, today):
    # Ledger spec "Split edit on a scheduled group refused", as written: the installment group 信貸 每月還款 #1/36.
    instance, repayment_id, _ = _posted_period(db_session, seed, loan, today)
    group_id = db_session.get(LedgerEntry, repayment_id).group_id

    response = client.put(
        f"/splits/{group_id}",
        json={"entry_date": "2026-11-09", "members": [{"account_id": loan.bank.id, "kind": "expense", "amount": "100"}]},
    )

    assert response.status_code == 409 and str(instance.id) in response.json()["message"]
    assert db_session.get(LedgerEntry, repayment_id) is not None


def test_split_edit_on_a_scheduled_group_refused(client, db_session, seed, today):
    # The same rule for a split-kind schedule group (two plain lines).
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    bundle = seed.definition([seed.line("expense", card, "390"), seed.line("expense", card, "149")], name="串流組合")
    instance = seed.instance(bundle, 1, date(2026, 10, 22))
    first_id, _ = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    group_id = db_session.get(LedgerEntry, first_id).group_id

    response = client.put(
        f"/splits/{group_id}",
        json={"entry_date": "2026-10-22", "members": [{"account_id": card.id, "kind": "expense", "amount": "100"}]},
    )

    assert response.status_code == 409 and str(instance.id) in response.json()["message"]


def test_deleting_a_scheduled_split_reopens_the_period(client, db_session, seed, today):
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    bundle = seed.definition([seed.line("expense", card, "390"), seed.line("expense", card, "149")], name="串流組合")
    instance = seed.instance(bundle, 1, date(2026, 10, 22))
    first_id, _ = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    group_id = db_session.get(LedgerEntry, first_id).group_id

    assert client.delete(f"/splits/{group_id}").status_code == 204

    assert _row(db_session, instance.id).status == "pending"


def test_entry_delete_touching_a_period_is_refused_during_an_import(client, db_session, seed, loan, today, pg_engine):
    _, _, interest_id = _posted_period(db_session, seed, loan, today)
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            response = client.delete(f"/entries/{interest_id}")
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    assert (response.status_code, response.json()["message"]) == (409, "import_running")
    assert db_session.get(LedgerEntry, interest_id) is not None


def test_reset_to_pending_clears_a_misaligned_override(client, db_session, seed, today):
    # A posted row's amount_override carries the amounts it was posted with (pinned before a template change). Back
    # to pending, an override whose line count no longer matches the template is cleared; a matching one is kept.
    today(date(2026, 10, 23))
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "390")], name="串流")
    first = seed.entry(card, "-390", day=date(2026, 9, 22), source="schedule")
    second = seed.entry(card, "-400", day=date(2026, 10, 22), source="schedule")
    pinned = seed.instance(definition, 1, date(2026, 9, 22), status="posted", entries=[first],
                           amount_override=["390", "149"])
    edited = seed.instance(definition, 2, date(2026, 10, 22), status="posted", entries=[second], amount_override=["400"])
    db_session.commit()

    assert client.delete(f"/entries/{first.id}").status_code == 204
    assert client.delete(f"/entries/{second.id}").status_code == 204

    assert (_row(db_session, pinned.id).status, _row(db_session, pinned.id).amount_override) == ("pending", None)
    assert (_row(db_session, edited.id).status, _row(db_session, edited.id).amount_override) == ("pending", ["400"])
