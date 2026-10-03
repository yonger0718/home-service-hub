"""Settle (收款 / 還款), refund and balance adjustment; all respect the cutover lock on their target."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.main import app
from app.models import LedgerEntry
from app.schemas.writes import BalanceAdjustmentIn, RefundIn, SettleIn, SplitIn
from app.services import entry_write_service as ews
from app.services import ledger_service
from app.services import settlement_service as st
from app.services import split_service
from app.services.edit_lock import EditLockedError
from app.services.errors import ValidationError
from tests.helpers import race

DAY = date(2026, 9, 1)
LATER = "2026-09-10"


def _balance(db, account_id) -> Decimal:
    db.expire_all()
    return ledger_service.account_balance(db, account_id)


def _error_fields(response) -> set[str]:
    return {str(error["loc"][-1]) for error in response.json()["detail"]}


def test_partial_collection(client, db_session, seed):
    # Spec: "Partial collection".
    card, wallet = seed.account("C"), seed.account("W")
    alan = seed.counterparty("Alan")
    receivable = seed.entry(card, "-420", kind="receivable", counterparty_id=alan.id)
    db_session.commit()
    receivable_id, wallet_id, alan_id = receivable.id, wallet.id, alan.id

    response = client.post(
        f"/entries/{receivable_id}/settle", json={"account_id": wallet_id, "amount": "200", "entry_date": LATER}
    )

    assert response.status_code == 201
    body = response.json()
    assert (body["kind"], body["amount"], body["account_id"]) == ("receivable", "200.0000", wallet_id)
    collection = db_session.get(LedgerEntry, body["id"])
    assert (collection.settles_entry_id, collection.counterparty_id, collection.name, collection.source) == (
        receivable_id, alan_id, "收款", "manual",
    )
    assert collection.is_settlement is True
    assert _balance(db_session, wallet_id) == Decimal("200")
    assert st.open_amount(db_session, db_session.get(LedgerEntry, receivable_id)) == Decimal("220")
    detail = client.get(f"/entries/{receivable_id}").json()
    assert (detail["open_amount"], detail["is_settled"]) == ("220.0000", False)


def test_settling_a_closed_debt_is_refused(client, db_session, seed):
    card, wallet = seed.account("C"), seed.account("W")
    receivable = seed.entry(card, "-420", kind="receivable", counterparty_id=seed.counterparty("Alan").id)
    receivable.is_closed = True
    db_session.commit()

    response = client.post(
        f"/entries/{receivable.id}/settle", json={"account_id": wallet.id, "amount": "100", "entry_date": LATER}
    )

    assert response.status_code == 422 and _error_fields(response) == {"is_closed"}
    assert "已結清" in response.json()["detail"][0]["msg"]
    detail = client.get(f"/entries/{receivable.id}").json()
    assert (detail["is_closed"], detail["open_amount"], detail["is_settled"]) == (True, "0.0000", True)


def test_a_settlement_in_another_currency_never_reduces_the_open_amount(client, db_session, seed):
    card, yen = seed.account("C"), seed.account("日幣", currency="JPY")
    alan = seed.counterparty("Alan")
    receivable = seed.entry(card, "-100", kind="receivable", counterparty_id=alan.id)
    seed.entry(yen, "100", kind="receivable", counterparty_id=alan.id, settles_entry_id=receivable.id, is_settlement=True)
    same = seed.entry(card, "-50", kind="receivable", counterparty_id=alan.id)
    seed.entry(card, "50", kind="receivable", counterparty_id=alan.id, settles_entry_id=same.id, is_settlement=True)
    db_session.commit()

    detail = client.get(f"/entries/{receivable.id}").json()
    assert (detail["open_amount"], detail["is_settled"]) == ("100.0000", False)
    assert st.open_amount(db_session, receivable) == Decimal("100")
    settled = client.get(f"/entries/{same.id}").json()
    assert (settled["open_amount"], settled["is_settled"]) == ("0.0000", True)
    rows = {row["name"]: row for row in client.get("/counterparties").json()}
    assert rows["Alan"]["open_amounts"] == [{"currency": "TWD", "amount": "100.0000"}]


def test_repayment_of_a_payable_is_negative_and_settles_it_fully(db_session, seed):
    bank = seed.account("銀行", opening="5000")
    alan = seed.counterparty("Alan")
    payable = seed.entry(bank, "1000", kind="payable", counterparty_id=alan.id)

    repayment_id = st.settle(db_session, payable.id, SettleIn(account_id=bank.id, amount="1000", entry_date=DAY))
    db_session.flush()

    repayment = db_session.get(LedgerEntry, repayment_id)
    assert (repayment.kind, repayment.amount, repayment.counterparty_id, repayment.name) == (
        "payable", Decimal("-1000"), alan.id, "還款",
    )
    assert st.open_amount(db_session, payable) == 0


def test_over_settlement_refused(client, db_session, seed):
    # Spec: "Over-settlement refused".
    card, wallet = seed.account("C"), seed.account("W")
    alan = seed.counterparty("Alan")
    receivable = seed.entry(card, "-420", kind="receivable", counterparty_id=alan.id)
    seed.entry(
        wallet, "200", kind="receivable", counterparty_id=alan.id, settles_entry_id=receivable.id, is_settlement=True
    )
    db_session.commit()

    response = client.post(
        f"/entries/{receivable.id}/settle", json={"account_id": wallet.id, "amount": "300", "entry_date": LATER}
    )

    assert response.status_code == 422 and _error_fields(response) == {"amount"}


def test_settlement_in_another_currency_refused(client, db_session, seed):
    # Spec: "Settlement in another currency refused".
    card, yen = seed.account("C"), seed.account("日幣", currency="JPY")
    alan = seed.counterparty("Alan")
    receivable = seed.entry(card, "-420", kind="receivable", counterparty_id=alan.id)
    db_session.commit()

    response = client.post(
        f"/entries/{receivable.id}/settle", json={"account_id": yen.id, "amount": "100", "entry_date": LATER}
    )

    assert response.status_code == 422 and _error_fields(response) == {"currency"}


def test_only_receivables_and_payables_are_settled(db_session, seed):
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    expense = seed.entry(wallet, "-100")
    receivable = seed.entry(wallet, "-420", kind="receivable", counterparty_id=alan.id)
    collection = seed.entry(
        wallet, "100", kind="receivable", counterparty_id=alan.id, settles_entry_id=receivable.id, is_settlement=True
    )

    for target in (expense, collection):
        with pytest.raises(ValidationError) as exc:
            st.settle(db_session, target.id, SettleIn(account_id=wallet.id, amount="1", entry_date=DAY))
        assert exc.value.field == "kind"


def test_partial_refund_to_another_account(client, db_session, seed):
    # Spec: "Partial refund to another account".
    card, wallet = seed.account("C"), seed.account("W")
    shoes = seed.category("鞋子")
    expense = seed.entry(card, "-1200", category_id=shoes.id, name="球鞋", merchant="運動用品店")
    db_session.commit()
    expense_id, wallet_id, shoes_id = expense.id, wallet.id, shoes.id

    response = client.post(
        f"/entries/{expense_id}/refund", json={"account_id": wallet_id, "amount": "570", "entry_date": LATER}
    )

    assert response.status_code == 201
    refund = db_session.get(LedgerEntry, response.json()["id"])
    assert (refund.kind, refund.amount, refund.account_id, refund.refunds_entry_id) == (
        "refund", Decimal("570"), wallet_id, expense_id,
    )
    assert (refund.category_id, refund.name, refund.merchant, refund.source) == (shoes_id, "球鞋", "運動用品店", "manual")
    assert _balance(db_session, wallet_id) == Decimal("570")
    assert st.refunded_amount(db_session, db_session.get(LedgerEntry, expense_id)) == Decimal("570")
    assert client.get(f"/entries/{expense_id}").json()["refunded_amount"] == "570.0000"


def test_refund_defaults_to_the_original_account_and_is_capped(db_session, seed):
    card = seed.account("C")
    expense = seed.entry(card, "-1200")
    st.refund(db_session, expense.id, RefundIn(amount="570", entry_date=DAY))
    db_session.flush()

    with pytest.raises(ValidationError) as exc:
        st.refund(db_session, expense.id, RefundIn(amount="631", entry_date=DAY))
    assert exc.value.field == "amount"

    last_id = st.refund(db_session, expense.id, RefundIn(amount="630", entry_date=DAY))
    db_session.flush()
    assert db_session.get(LedgerEntry, last_id).account_id == card.id
    assert st.refunded_amount(db_session, expense) == Decimal("1200")


def test_refund_rules_currency_and_kind(db_session, seed):
    card, yen = seed.account("C"), seed.account("日幣", currency="JPY")
    expense = seed.entry(card, "-1200")
    income = seed.entry(card, "500", kind="income")

    with pytest.raises(ValidationError) as currency:
        st.refund(db_session, expense.id, RefundIn(account_id=yen.id, amount="10", entry_date=DAY))
    with pytest.raises(ValidationError) as kind:
        st.refund(db_session, income.id, RefundIn(amount="10", entry_date=DAY))

    assert (currency.value.field, kind.value.field) == ("currency", "kind")


def test_refunding_a_group_member_is_allowed_and_a_group_has_no_refund_route(client, db_session, seed):
    wallet = seed.account()
    group_id = split_service.create_split(
        db_session,
        SplitIn(entry_date=DAY, members=[
            {"account_id": wallet.id, "kind": "expense", "amount": "300"},
            {"account_id": wallet.id, "kind": "expense", "amount": "200"},
        ]),
    )
    db_session.commit()
    first_member = split_service.member_ids(db_session, group_id)[0]

    member_refund = client.post(f"/entries/{first_member}/refund", json={"amount": "300", "entry_date": LATER})
    group_refund = client.post(f"/splits/{group_id}/refund", json={"amount": "300", "entry_date": LATER})

    assert member_refund.status_code == 201
    assert group_refund.status_code in (404, 405)
    assert not [route for route in app.routes if getattr(route, "path", "").startswith("/splits/") and "refund" in route.path]


def test_balance_adjustment_stores_the_delta(client, db_session, seed):
    # Spec: "Balance adjustment stores the delta".
    account = seed.account("A", opening="24806")
    db_session.commit()
    account_id = account.id

    response = client.post(
        "/balance-adjustments", json={"account_id": account_id, "target_balance": "24798", "entry_date": DAY.isoformat()}
    )

    assert response.status_code == 201
    assert (response.json()["kind"], response.json()["amount"]) == ("balance_adjustment", "-8.0000")
    adjustment = db_session.get(LedgerEntry, response.json()["id"])
    assert adjustment.description == "調整後餘額 24798"
    assert adjustment.source == "manual"
    assert _balance(db_session, account_id) == Decimal("24798")


def test_balance_adjustment_keeps_the_owner_note_and_refuses_zero_delta(db_session, seed):
    account = seed.account("A", opening="100")

    entry_id = ews.create_balance_adjustment(
        db_session, BalanceAdjustmentIn(account_id=account.id, target_balance="150.5", entry_date=DAY, description="對帳")
    )
    db_session.flush()

    assert db_session.get(LedgerEntry, entry_id).description == "對帳\n調整後餘額 150.5"
    with pytest.raises(ValidationError) as exc:
        ews.create_balance_adjustment(
            db_session, BalanceAdjustmentIn(account_id=account.id, target_balance="150.5", entry_date=DAY)
        )
    assert exc.value.field == "target_balance"


def test_settle_and_refund_refuse_locked_target(client, db_session, seed, monkeypatch):
    # Review Focus 2: side doors (settle, refund, group members) obey the lock like PUT.
    card, wallet = seed.account("C"), seed.account("W")
    alan = seed.counterparty("Alan")
    receivable = seed.entry(card, "-420", kind="receivable", counterparty_id=alan.id, source="moze_backup", moze_id="rec-1")
    expense = seed.entry(card, "-1200", source="moze_backup", moze_id="rec-2")
    group = seed.group(moze_id="pkg-1")
    member = seed.entry(card, "-300", group_id=group.id)
    db_session.commit()
    ids = {"receivable": receivable.id, "expense": expense.id, "member": member.id, "wallet": wallet.id}
    settle_body = {"account_id": ids["wallet"], "amount": "100", "entry_date": LATER}
    refund_body = {"amount": "100", "entry_date": LATER}

    refused = [
        client.post(f"/entries/{ids['receivable']}/settle", json=settle_body),
        client.post(f"/entries/{ids['expense']}/refund", json=refund_body),
        client.post(f"/entries/{ids['member']}/refund", json=refund_body),
    ]
    with pytest.raises(EditLockedError):
        st.settle(db_session, ids["receivable"], SettleIn(**settle_body))
    with pytest.raises(EditLockedError):
        st.refund(db_session, ids["expense"], RefundIn(**refund_body))
    db_session.rollback()  # release the FOR UPDATE row locks the refused calls took, as a request's session does

    assert [(r.status_code, r.json()["message"]) for r in refused] == [(409, "locked_until_cutover")] * 3
    db_session.expire_all()
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == ids["wallet"])).all() == []

    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert client.post(f"/entries/{ids['receivable']}/settle", json=settle_body).status_code == 201
    assert client.post(f"/entries/{ids['expense']}/refund", json=refund_body).status_code == 201
    assert client.post(f"/entries/{ids['member']}/refund", json=refund_body).status_code == 201


def test_concurrent_refunds_cannot_exceed_original(pg_engine, db_session, seed):
    # Plan review P1: both requests read refundable 100 without the row lock and both refunded 80.
    expense = seed.entry(seed.account("C"), "-100")
    db_session.commit()
    expense_id = expense.id

    refund_80 = lambda db: st.refund(db, expense_id, RefundIn(amount="80", entry_date=DAY))  # noqa: E731
    result = race(pg_engine, refund_80, refund_80)

    assert isinstance(result, ValidationError) and result.field == "amount"
    db_session.expire_all()
    assert st.refunded_amount(db_session, db_session.get(LedgerEntry, expense_id)) == Decimal("80")
    refunds = db_session.scalars(select(LedgerEntry).where(LedgerEntry.refunds_entry_id == expense_id)).all()
    assert len(refunds) == 1


def test_concurrent_settlements_cannot_exceed_open_amount(pg_engine, db_session, seed):
    wallet = seed.account("W")
    alan = seed.counterparty("Alan")
    receivable = seed.entry(wallet, "-100", kind="receivable", counterparty_id=alan.id)
    db_session.commit()
    receivable_id, wallet_id = receivable.id, wallet.id

    settle_80 = lambda db: st.settle(db, receivable_id, SettleIn(account_id=wallet_id, amount="80", entry_date=DAY))  # noqa: E731
    result = race(pg_engine, settle_80, settle_80)

    assert isinstance(result, ValidationError) and result.field == "amount"
    db_session.expire_all()
    assert st.open_amount(db_session, db_session.get(LedgerEntry, receivable_id)) == Decimal("20")
    settlements = db_session.scalars(select(LedgerEntry).where(LedgerEntry.settles_entry_id == receivable_id)).all()
    assert len(settlements) == 1


def test_concurrent_balance_adjustments_apply_the_delta_once(pg_engine, db_session, seed):
    wallet = seed.account("W", opening="1000")
    seed.entry(wallet, "-100")
    ews.system_category_id(db_session, "balance_adjustment")  # exists already, as after the first adjustment ever
    db_session.commit()
    wallet_id = wallet.id

    adjust = lambda db: ews.create_balance_adjustment(  # noqa: E731
        db, BalanceAdjustmentIn(account_id=wallet_id, target_balance="500", entry_date=DAY)
    )
    result = race(pg_engine, adjust, adjust)

    # The second request waits for the first's account lock, then sees the balance already at the target.
    assert isinstance(result, ValidationError) and result.field == "target_balance"
    db_session.expire_all()
    assert ledger_service.account_balance(db_session, wallet_id) == Decimal("500")
    adjustments = db_session.scalars(select(LedgerEntry).where(LedgerEntry.kind == "balance_adjustment")).all()
    assert [entry.amount for entry in adjustments] == [Decimal("-400")]
