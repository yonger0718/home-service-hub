"""PUT /entries/{id} keeps the fee / discount children it does not change (issue #313, #293 option B).

Before #313 every full PUT deleted the children and inserted new rows, so a statement claim on a fee child pointed at
a deleted row (entry_id SET NULL) after any edit of the parent, even a save with no change. Children are now
reconciled by kind: an unchanged child keeps its id, row and claim; a changed one is updated in place (a structural
change, such as its amount, still releases the claim at the next sweep, as for any edited covered entry); a dropped
one is deleted; a new one is inserted. Synthetic data only."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import LedgerEntry, StatementCoverage
from app.services.reconciliation_actions import engine
from tests.integration.statement_fixtures import _active, _card, _cases, _line, _reconcile, _submit, run  # noqa: F401

DAY = date(2026, 9, 3)
pytestmark = pytest.mark.usefixtures("dirty_on")  # production runs with the dirty triggers on


def _claimed_purchase(seed, db, run, *, fee_name="手續費"):
    """A 1,200 purchase with a 30 fee child, both claimed by the card's statement (the fee by an owner match)."""
    card = _card(seed, db)
    parent = seed.entry(card, "-1200", day=DAY, merchant="海外店")
    child = seed.entry(card, "-30", kind="fee", day=DAY, parent_entry_id=parent.id, name=fee_name)
    db.commit()
    stmt, (purchase, fee_line), _ = _submit(db, run, card, [_line(1, DAY, "海外店", 1200), _line(2, DAY, "", 30)])
    (case,) = [c for c in _cases(db, stmt.id, "open") if c.kind == "line_unmatched" and c.line_id == fee_line.id]
    engine.apply_action(db, case_id=case.id, action="match",
                        params={"line_id": fee_line.id, "entry_id": child.id}, actor="owner", label="owner-test")
    db.commit()
    claims = {(c.line_id, c.entry_id) for c in _active(db, stmt.id)}
    assert claims == {(purchase.id, parent.id), (fee_line.id, child.id)}
    return card, parent.id, child.id, stmt, fee_line


def _payload(card, **over):
    body = {"account_id": card.id, "kind": "expense", "amount": "1200", "entry_date": DAY.isoformat(),
            "merchant": "海外店", "fee": {"amount": "30", "name": "手續費"}}
    return {**body, **over}


def _children(db, parent_id):
    db.expire_all()
    return [(c.id, c.kind, c.amount, c.name) for c in db.scalars(
        select(LedgerEntry).where(LedgerEntry.parent_entry_id == parent_id).order_by(LedgerEntry.id))]


def _claims(db, stmt):
    db.expire_all()
    return {(c.line_id, c.entry_id) for c in _active(db, stmt.id)}


def test_a_put_with_no_change_keeps_the_fee_child_and_its_claim(client, seed, db_session, run):
    card, parent_id, child_id, stmt, _ = _claimed_purchase(seed, db_session, run)
    before = _claims(db_session, stmt)

    response = client.put(f"/entries/{parent_id}", json=_payload(card))

    assert response.status_code == 200, response.text
    assert _children(db_session, parent_id) == [(child_id, "fee", Decimal("-30.0000"), "手續費")]
    assert _claims(db_session, stmt) == before
    _reconcile(db_session, stmt.id)
    assert _claims(db_session, stmt) == before


def test_a_cosmetic_parent_edit_keeps_both_claims(client, seed, db_session, run):
    card, parent_id, child_id, stmt, _ = _claimed_purchase(seed, db_session, run)
    before = _claims(db_session, stmt)

    response = client.put(f"/entries/{parent_id}", json=_payload(card, name="晚餐", description="改名", tags=["旅行"]))

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "晚餐"
    assert _children(db_session, parent_id) == [(child_id, "fee", Decimal("-30.0000"), "手續費")]
    _reconcile(db_session, stmt.id)
    assert _claims(db_session, stmt) == before


def test_a_renamed_fee_is_updated_in_place_and_keeps_its_claim(client, seed, db_session, run):
    card, parent_id, child_id, stmt, _ = _claimed_purchase(seed, db_session, run)
    before = _claims(db_session, stmt)

    response = client.put(f"/entries/{parent_id}", json=_payload(card, fee={"amount": "30", "name": "運費"}))

    assert response.status_code == 200, response.text
    assert _children(db_session, parent_id) == [(child_id, "fee", Decimal("-30.0000"), "運費")]
    _reconcile(db_session, stmt.id)
    assert _claims(db_session, stmt) == before


def test_a_fee_without_a_name_keeps_the_stored_name(client, seed, db_session, run):
    """國外交易手續費 is how attach_fee and the fee proposal recognise the FX fee child: a nameless payload keeps it."""
    card, parent_id, child_id, stmt, _ = _claimed_purchase(seed, db_session, run, fee_name="國外交易手續費")
    before = _claims(db_session, stmt)

    response = client.put(f"/entries/{parent_id}", json=_payload(card, fee={"amount": "30"}))

    assert response.status_code == 200, response.text
    assert _children(db_session, parent_id) == [(child_id, "fee", Decimal("-30.0000"), "國外交易手續費")]
    _reconcile(db_session, stmt.id)
    assert _claims(db_session, stmt) == before


def test_a_changed_fee_amount_updates_in_place_and_the_sweep_releases_the_claim(client, seed, db_session, run):
    card, parent_id, child_id, stmt, fee_line = _claimed_purchase(seed, db_session, run)

    response = client.put(f"/entries/{parent_id}", json=_payload(card, fee={"amount": "35", "name": "手續費"}))

    assert response.status_code == 200, response.text
    assert _children(db_session, parent_id) == [(child_id, "fee", Decimal("-35.0000"), "手續費")]
    # until the sweep the claim still names the same (live) row; the structural update then releases it
    assert (fee_line.id, child_id) in _claims(db_session, stmt)
    _reconcile(db_session, stmt.id)
    assert not any(line == fee_line.id for line, _ in _claims(db_session, stmt))
    db_session.expire_all()
    released = db_session.scalars(select(StatementCoverage).where(
        StatementCoverage.line_id == fee_line.id, StatementCoverage.entry_id == child_id)).all()
    assert released and all(c.status != "active" for c in released)


def test_a_moved_parent_moves_its_child_in_place(client, seed, db_session):
    card = seed.account("卡")
    other = seed.account("另一張卡")
    db_session.commit()
    created = client.post("/entries", json=_payload(card))
    assert created.status_code in (200, 201), created.text
    parent_id = created.json()["id"]
    ((child_id, *_),) = _children(db_session, parent_id)

    response = client.put(f"/entries/{parent_id}", json=_payload(other, entry_date="2026-09-05"))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    child = db_session.get(LedgerEntry, child_id)
    assert (child.account_id, child.entry_date, child.posted_date, child.parent_entry_id) == (
        other.id, date(2026, 9, 5), date(2026, 9, 5), parent_id)


def test_dropping_the_fee_deletes_it_and_adding_a_discount_inserts_one(client, seed, db_session, run):
    card, parent_id, child_id, stmt, fee_line = _claimed_purchase(seed, db_session, run)

    response = client.put(f"/entries/{parent_id}", json=_payload(card, fee=None, discount={"amount": "50"}))

    assert response.status_code == 200, response.text
    children = _children(db_session, parent_id)
    assert [(kind, amount, name) for _, kind, amount, name in children] == [("discount", Decimal("50.0000"), "折扣")]
    assert db_session.get(LedgerEntry, child_id) is None
    _reconcile(db_session, stmt.id)
    assert not any(line == fee_line.id for line, _ in _claims(db_session, stmt))


def test_two_fee_children_sent_back_as_their_sum_are_left_alone(client, seed, db_session):
    """The SPA folds several children of one kind into one value (summed amount, the first child's name)."""
    card = seed.account("卡")
    parent = seed.entry(card, "-1200", day=DAY, merchant="海外店")
    first = seed.entry(card, "-30", kind="fee", day=DAY, parent_entry_id=parent.id, name="運費")
    second = seed.entry(card, "-15", kind="fee", day=DAY, parent_entry_id=parent.id, name="國外交易手續費")
    db_session.commit()
    ids = [first.id, second.id]

    same = client.put(f"/entries/{parent.id}", json=_payload(card, fee={"amount": "45", "name": "運費"}))

    assert same.status_code == 200, same.text
    assert [c[0] for c in _children(db_session, parent.id)] == ids

    changed = client.put(f"/entries/{parent.id}", json=_payload(card, fee={"amount": "50", "name": "運費"}))

    assert changed.status_code == 200, changed.text
    children = _children(db_session, parent.id)
    assert [(kind, amount, name) for _, kind, amount, name in children] == [("fee", Decimal("-50.0000"), "運費")]
    assert not {c[0] for c in children} & set(ids)


def _transfer_legs(db, group_id):
    db.expire_all()
    legs = {leg.kind: leg.id for leg in db.scalars(select(LedgerEntry).where(
        LedgerEntry.transfer_group_id == uuid.UUID(str(group_id)), LedgerEntry.parent_entry_id.is_(None)))}
    return legs["transfer_out"], legs["transfer_in"]


def test_a_transfer_put_keeps_unchanged_leg_children_and_updates_changed_ones_in_place(client, seed, db_session):
    """PUT /transfers goes through the same child reconciliation (#313): an unchanged out fee keeps its row, a
    changed one is updated in place, a dropped one is deleted, a new in-leg discount is inserted."""
    a, b = seed.account("A", opening="5000"), seed.account("B")
    db_session.commit()
    body = {"from_account_id": a.id, "to_account_id": b.id, "out_amount": "3000", "entry_date": DAY.isoformat(),
            "out_fee": {"amount": "15"}}
    group = client.post("/transfers", json=body).json()["transfer_group_id"]
    out_id, in_id = _transfer_legs(db_session, group)
    ((fee_id, *_),) = _children(db_session, out_id)

    same = client.put(f"/transfers/{group}", json={**body, "description": "改備註"})
    assert same.status_code == 200, same.text
    assert _children(db_session, out_id) == [(fee_id, "fee", Decimal("-15.0000"), "手續費")]

    changed = client.put(f"/transfers/{group}", json={**body, "out_fee": {"amount": "20"},
                                                       "in_discount": {"amount": "5"}})
    assert changed.status_code == 200, changed.text
    assert _children(db_session, out_id) == [(fee_id, "fee", Decimal("-20.0000"), "手續費")]
    assert [(k, amt) for _, k, amt, _ in _children(db_session, in_id)] == [("discount", Decimal("5.0000"))]

    dropped = client.put(f"/transfers/{group}", json={**body, "out_fee": None})
    assert dropped.status_code == 200, dropped.text
    assert _children(db_session, out_id) == [] and _children(db_session, in_id) == []
