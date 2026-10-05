"""Schedule links on entries (spec "Schedule links on entries", "Loan summary" through GET /entries/{id})."""

from datetime import date
from decimal import Decimal


def test_entry_rows_carry_the_schedule_link(client, db_session, seed, today):
    # Spec "Pill data on a posted entry".
    today(date(2027, 3, 1))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id)
    loan = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id)], kind="installment", name="信貸 每月還款",
        times=36,
    )
    repayment = seed.entry(bank, "-8333", kind="payable", counterparty_id=lender.id, settles_entry_id=payable.id,
                           is_settlement=True, day=date(2027, 2, 9), source="schedule")
    fifth = seed.instance(loan, 5, date(2027, 2, 9), status="posted", entries=[repayment])
    db_session.commit()

    rows = {row["id"]: row for row in client.get("/entries").json()["items"]}

    link = rows[repayment.id]["schedule"]
    assert (link["kind"], link["seq"], link["times"], link["is_partial"], link["instance_id"], link["definition_id"]) == (
        "installment", 5, 36, False, fifth.id, loan.id,
    )
    assert rows[payable.id]["schedule"] is None


def test_passbook_and_detail_carry_the_link(client, db_session, seed, today):
    # Spec "Unlimited recurring pill" data: times null.
    today(date(2026, 10, 30))
    card = seed.account("範例卡")
    netflix = seed.definition([seed.line("expense", card, "390")])
    expense = seed.entry(card, "-390", day=date(2026, 10, 22), source="schedule")
    seed.instance(netflix, 25, date(2026, 10, 22), status="posted", entries=[expense])
    db_session.commit()

    passbook = client.get(f"/accounts/{card.id}/entries").json()["items"][0]["schedule"]
    detail = client.get(f"/entries/{expense.id}").json()

    assert (passbook["kind"], passbook["seq"], passbook["times"]) == ("recurring", 25, None)
    assert detail["schedule"]["seq"] == 25 and detail["loan_schedule"] is None


def test_loan_detail_after_three_periods(client, db_session, seed, today):
    # Spec "Loan detail after three periods".
    today(date(2027, 1, 20))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id)
    loan = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id)], kind="installment", name="信貸 每月還款",
        anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    for seq, day in enumerate((date(2026, 11, 9), date(2026, 12, 9), date(2027, 1, 9)), start=1):
        repayment = seed.entry(bank, "-8333", kind="payable", counterparty_id=lender.id, settles_entry_id=payable.id,
                               is_settlement=True, day=day, source="schedule")
        seed.instance(loan, seq, day, status="posted", entries=[repayment])
    seed.instance(loan, 4, date(2027, 2, 9))
    db_session.commit()

    body = client.get(f"/entries/{payable.id}").json()["loan_schedule"]

    assert Decimal(body["remaining"]) == Decimal("-275001") and Decimal(body["repaid"]) == Decimal("24999")
    assert (body["posted_count"], body["times"], body["next_due_date"], body["definition_id"]) == (3, 36, "2027-02-09", loan.id)
    assert (body["name"], body["status"], body["posting_mode"], body["needs_check"]) == ("信貸 每月還款", "active", "auto", False)


def test_loan_schedule_is_null_without_a_definition(client, db_session, seed, today):
    today(date(2026, 10, 3))
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    receivable = seed.entry(wallet, "-420", kind="receivable", counterparty_id=alan.id)
    db_session.commit()
    assert client.get(f"/entries/{receivable.id}").json()["loan_schedule"] is None
