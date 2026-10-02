"""Transfers: both legs in one group, per-leg children, cross-currency rates derived from the two amounts."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Category, EntryRewardRule, LedgerEntry
from app.schemas.writes import TransferIn
from app.services import ledger_service
from app.services import transfer_service as ts
from app.services.edit_lock import EditLockedError
from app.services.errors import ValidationError

DAY = date(2026, 9, 1)


def _body(a, b, **fields) -> dict:
    body = {"from_account_id": a.id, "to_account_id": b.id, "out_amount": "3000", "entry_date": DAY.isoformat()}
    body.update(fields)
    return body


def _legs(db, group_id) -> tuple[LedgerEntry, LedgerEntry]:
    db.expire_all()
    legs = {
        leg.kind: leg
        for leg in db.scalars(
            select(LedgerEntry).where(
                LedgerEntry.transfer_group_id == uuid.UUID(str(group_id)), LedgerEntry.parent_entry_id.is_(None)
            )
        )
    }
    return legs["transfer_out"], legs["transfer_in"]


def _children(db, parent_id) -> dict[str, LedgerEntry]:
    return {c.kind: c for c in db.scalars(select(LedgerEntry).where(LedgerEntry.parent_entry_id == parent_id))}


def _balance(db, account_id) -> Decimal:
    db.expire_all()
    return ledger_service.account_balance(db, account_id)


def _error_fields(response) -> set[str]:
    return {str(error["loc"][-1]) for error in response.json()["detail"]}


def test_same_currency_transfer_with_out_fee(client, db_session, seed):
    # Spec: "Same-currency transfer".
    a, b = seed.account("A", opening="5000"), seed.account("B")
    db_session.commit()
    a_id, b_id = a.id, b.id

    response = client.post("/transfers", json=_body(a, b, out_fee={"amount": "15"}))

    assert response.status_code == 201
    body = response.json()
    out_leg, in_leg = _legs(db_session, body["transfer_group_id"])
    assert (body["out_entry_id"], body["in_entry_id"]) == (out_leg.id, in_leg.id)
    assert (out_leg.amount, out_leg.account_id, in_leg.amount, in_leg.account_id) == (
        Decimal("-3000"), a_id, Decimal("3000"), b_id,
    )
    assert out_leg.source == in_leg.source == "manual"
    assert (out_leg.original_amount, out_leg.fx_rate, in_leg.original_amount, in_leg.fx_rate) == (None, None, None, None)
    assert _children(db_session, out_leg.id)["fee"].amount == Decimal("-15")
    assert _children(db_session, in_leg.id) == {}
    assert (_balance(db_session, a_id), _balance(db_session, b_id)) == (Decimal("1985"), Decimal("3000"))


def test_cross_currency_transfer_stores_the_other_leg_and_derived_rates(db_session, seed):
    # Spec: "Cross-currency transfer".
    twd, jpy = seed.account("台幣"), seed.account("日幣", currency="JPY")

    group_id = ts.create_transfer(
        db_session,
        TransferIn(from_account_id=twd.id, to_account_id=jpy.id, out_amount="10000", in_amount="46200", entry_date=DAY),
    )
    db_session.flush()

    out_leg, in_leg = _legs(db_session, group_id)
    assert (out_leg.amount, out_leg.currency, in_leg.amount, in_leg.currency) == (
        Decimal("-10000"), "TWD", Decimal("46200"), "JPY",
    )
    assert (in_leg.original_amount, in_leg.original_currency, in_leg.fx_rate, in_leg.fx_source) == (
        Decimal("10000"), "TWD", Decimal("4.62"), "manual",
    )
    assert (out_leg.original_amount, out_leg.original_currency, out_leg.fx_rate, out_leg.fx_source) == (
        Decimal("-46200"), "JPY", Decimal("0.2164502165"), "manual",
    )
    # The amounts are the source of truth; the derived rate reproduces them only within half a display unit.
    assert out_leg.original_amount * out_leg.fx_rate != out_leg.amount
    assert ts.fx_consistent(out_leg.amount, out_leg.original_amount, out_leg.fx_rate, out_leg.currency)
    assert ts.fx_consistent(in_leg.amount, in_leg.original_amount, in_leg.fx_rate, in_leg.currency)


def test_fx_consistency_tolerance_is_half_a_display_unit():
    assert ts.fx_consistent(Decimal("-100"), Decimal("-3"), Decimal("33.5"), "TWD")  # 100.5 vs 100
    assert not ts.fx_consistent(Decimal("-100"), Decimal("-3"), Decimal("33.6"), "TWD")  # 100.8 vs 100
    assert ts.fx_consistent(Decimal("10.00"), Decimal("1000"), Decimal("0.010004"), "USD")  # 10.004
    assert not ts.fx_consistent(Decimal("10.00"), Decimal("1000"), Decimal("0.010006"), "USD")  # 10.006


def test_in_amount_rules(client, db_session, seed):
    twd, twd2, jpy = seed.account("台幣"), seed.account("台幣二"), seed.account("日幣", currency="JPY")
    db_session.commit()

    missing = client.post("/transfers", json=_body(twd, jpy))
    different = client.post("/transfers", json=_body(twd, twd2, in_amount="2999"))
    same = client.post("/transfers", json=_body(twd, twd2, in_amount="3000"))

    assert missing.status_code == 422 and _error_fields(missing) == {"in_amount"}
    assert different.status_code == 422 and _error_fields(different) == {"in_amount"}
    assert same.status_code == 201


def test_from_and_to_must_differ(client, db_session, seed):
    a = seed.account("A")
    db_session.commit()

    response = client.post("/transfers", json=_body(a, a))

    assert response.status_code == 422 and _error_fields(response) == {"to_account_id"}


def test_category_must_be_transfer_out_and_in_leg_gets_the_matching_category(db_session, seed):
    a, b = seed.account("A"), seed.account("B")
    food = seed.category("飲食")
    atm_out = seed.category("ATM", kind="transfer_out")
    atm_in = seed.category("ATM", kind="transfer_in")
    move_out = seed.category("轉帳", kind="transfer_out")
    card_out = seed.category("繳卡費", kind="transfer_out", parent=move_out)
    move_in = seed.category("轉帳", kind="transfer_in")
    card_in = seed.category("繳卡費", kind="transfer_in", parent=move_in)
    lonely = seed.category("投資", kind="transfer_out")

    with pytest.raises(ValidationError) as exc:
        ts.create_transfer(db_session, TransferIn(**_body(a, b, category_id=food.id)))
    assert exc.value.field == "category_id"

    expected = {atm_out.id: atm_in.id, card_out.id: card_in.id, lonely.id: None}
    for out_category, in_category in expected.items():
        group_id = ts.create_transfer(db_session, TransferIn(**_body(a, b, category_id=out_category)))
        db_session.flush()
        out_leg, in_leg = _legs(db_session, group_id)
        assert (out_leg.category_id, in_leg.category_id) == (out_category, in_category)


def test_category_remembers_the_from_account_and_project(db_session, seed):
    a, b = seed.account("A"), seed.account("B")
    atm = seed.category("ATM", kind="transfer_out")
    life = seed.project("生活")

    ts.create_transfer(db_session, TransferIn(**_body(a, b, category_id=atm.id, project_id=life.id)))
    db_session.flush()

    db_session.expire_all()
    remembered = db_session.get(Category, atm.id)
    assert (remembered.default_account_id, remembered.default_project_id) == (a.id, life.id)


def test_fee_and_discount_children_go_to_their_own_leg(db_session, seed):
    a, b = seed.account("A"), seed.account("B")

    group_id = ts.create_transfer(
        db_session,
        TransferIn(**_body(a, b, out_discount={"amount": "5", "name": "轉帳優惠"}, in_fee={"amount": "10"})),
    )
    db_session.flush()

    out_leg, in_leg = _legs(db_session, group_id)
    out_children, in_children = _children(db_session, out_leg.id), _children(db_session, in_leg.id)
    assert list(out_children) == ["discount"] and list(in_children) == ["fee"]
    assert (out_children["discount"].amount, out_children["discount"].name, out_children["discount"].account_id) == (
        Decimal("5"), "轉帳優惠", a.id,
    )
    assert (in_children["fee"].amount, in_children["fee"].name, in_children["fee"].account_id) == (
        Decimal("-10"), "手續費", b.id,
    )


def test_reward_rules_attach_to_the_out_leg_of_the_from_account(db_session, seed):
    card, bank = seed.account("卡"), seed.account("銀行")
    card_rule, bank_rule = seed.rule(card, "繳費回饋"), seed.rule(bank, "銀行回饋")

    with pytest.raises(ValidationError) as exc:
        ts.create_transfer(db_session, TransferIn(**_body(card, bank, reward_rule_ids=[bank_rule.id])))
    assert exc.value.field == "reward_rule_ids"

    group_id = ts.create_transfer(db_session, TransferIn(**_body(card, bank, reward_rule_ids=[card_rule.id])))
    db_session.flush()

    out_leg, in_leg = _legs(db_session, group_id)
    links = db_session.execute(select(EntryRewardRule.entry_id, EntryRewardRule.rule_id)).all()
    assert [tuple(link) for link in links] == [(out_leg.id, card_rule.id)]


def test_put_updates_both_legs_and_replaces_children(client, db_session, seed):
    a, b, c = seed.account("A"), seed.account("B"), seed.account("C")
    group_id = ts.create_transfer(db_session, TransferIn(**_body(a, b, out_fee={"amount": "15"})))
    db_session.commit()
    a_id, b_id, c_id = a.id, b.id, c.id
    before_out, before_in = _legs(db_session, group_id)
    before_ids = (before_out.id, before_in.id)

    response = client.put(
        f"/transfers/{group_id}",
        json={
            "from_account_id": a_id, "to_account_id": c_id, "out_amount": "1200",
            "entry_date": "2026-09-04", "name": "還錢", "in_discount": {"amount": "2"},
        },
    )

    assert response.status_code == 200
    out_leg, in_leg = _legs(db_session, group_id)
    assert (out_leg.id, in_leg.id) == before_ids == (response.json()["out_entry_id"], response.json()["in_entry_id"])
    assert (out_leg.amount, in_leg.amount, in_leg.account_id) == (Decimal("-1200"), Decimal("1200"), c_id)
    assert out_leg.entry_date == in_leg.entry_date == in_leg.posted_date == date(2026, 9, 4)
    assert out_leg.name == in_leg.name == "還錢"
    assert _children(db_session, out_leg.id) == {}
    assert _children(db_session, in_leg.id)["discount"].amount == Decimal("2")
    assert (_balance(db_session, a_id), _balance(db_session, b_id), _balance(db_session, c_id)) == (
        Decimal("-1200"), Decimal("0"), Decimal("1202"),
    )


def test_put_unknown_group_is_404(client, db_session, seed):
    a, b = seed.account("A"), seed.account("B")
    db_session.commit()

    assert client.put(f"/transfers/{uuid.uuid4()}", json=_body(a, b)).status_code == 404


def test_imported_transfer_locked_until_cutover(client, db_session, seed, monkeypatch):
    a, b = seed.account("A"), seed.account("B")
    group = uuid.uuid4()
    seed.entry(a, "-300", kind="transfer_out", transfer_group_id=group, source="moze_backup", moze_id="rec-1")
    seed.entry(b, "300", kind="transfer_in", transfer_group_id=group, source="moze_backup", moze_id="rec-2")
    db_session.commit()

    refused = client.put(f"/transfers/{group}", json=_body(a, b))
    with pytest.raises(EditLockedError):
        ts.update_transfer(db_session, group, TransferIn(**_body(a, b)))
    db_session.rollback()  # release the legs' FOR UPDATE locks, as the request's session close would

    assert (refused.status_code, refused.json()["message"]) == (409, "locked_until_cutover")
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert client.put(f"/transfers/{group}", json=_body(a, b)).status_code == 200
    out_leg, in_leg = _legs(db_session, group)
    assert (out_leg.amount, in_leg.amount, out_leg.source) == (Decimal("-3000"), Decimal("3000"), "manual")


def test_deleting_a_posted_transfer_leg_deletes_both(client, db_session, seed):
    a, b = seed.account("A"), seed.account("B")
    db_session.commit()
    body = client.post("/transfers", json=_body(a, b, in_fee={"amount": "10"})).json()

    assert client.delete(f"/entries/{body['out_entry_id']}").status_code == 204

    db_session.expire_all()
    assert db_session.scalars(select(LedgerEntry)).all() == []
