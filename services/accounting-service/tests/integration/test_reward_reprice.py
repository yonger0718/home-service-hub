"""GET / POST /entries/{id}/reward-reprice (issue #306, reward engine spec 5.1 "Reprice"): the owner's answer to the
`reward_posted` flag — reprice a purchase's posted rewards on its current figures, or keep them. Synthetic data."""
from decimal import Decimal
from uuid import uuid4

from app.models import LedgerEntry
from tests.integration.test_reward_engine_hooks import _card, _entry, _one, _payload, _post, _rule


def _flagged_purchase(client, db, *, amount="2000", **rule_columns):
    """A 1,000 purchase on a 1 % rule, its reward posted at 10, then edited to `amount` (flagged, 5.1)."""
    card = _card(db)
    rule = _rule(db, card, **rule_columns)
    db.commit()
    purchase_id = client.post("/entries", json=_payload(card, reward_rule_ids=[rule.id])).json()["id"]
    _post(db, _one(db, source_entry_id=purchase_id))
    db.commit()
    edited = client.put(f"/entries/{purchase_id}", json=_payload(card, amount=amount, reward_rule_ids=[rule.id]))
    assert edited.json()["review_reason"] == "reward_posted", edited.text
    return card, rule, purchase_id


def _reward_row(db, accrual):
    db.expire_all()
    return db.get(LedgerEntry, accrual.ledger_entry_id)


def test_preview_shows_booked_vs_new_and_writes_nothing(client, db_session):
    card, rule, purchase_id = _flagged_purchase(client, db_session)

    response = client.get(f"/entries/{purchase_id}/reward-reprice")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["entry_id"], body["flagged"], body["applied"]) == (purchase_id, True, [])
    (item,) = body["items"]
    assert (item["rule_id"], item["rule_name"], Decimal(item["booked"]), Decimal(item["new"]), item["status"]) == (
        rule.id, "測試規則", Decimal("10"), Decimal("20"), "reprice")
    accrual = _one(db_session, source_entry_id=purchase_id)
    assert (accrual.amount, _reward_row(db_session, accrual).amount) == (Decimal("10.0000"), Decimal("10.0000"))
    assert _entry(db_session, purchase_id).review_reason == "reward_posted"


def test_reprice_rewrites_the_accrual_and_its_reward_row_and_clears_the_flag(client, db_session):
    card, rule, purchase_id = _flagged_purchase(client, db_session)
    accrual_id = _one(db_session, source_entry_id=purchase_id).id

    response = client.post(f"/entries/{purchase_id}/reward-reprice", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["flagged"], body["applied"]) == (False, [accrual_id])
    assert [(i["status"], Decimal(i["booked"])) for i in body["items"]] == [("same", Decimal("20"))]
    accrual = _one(db_session, source_entry_id=purchase_id)
    assert (accrual.state, accrual.estimate, accrual.amount) == ("posted", Decimal("20.0000"), Decimal("20.0000"))
    assert _reward_row(db_session, accrual).amount == Decimal("20.0000")
    entry = _entry(db_session, purchase_id)
    assert (entry.needs_review, entry.review_reason) == (False, None)


def test_keep_clears_the_flag_and_changes_nothing(client, db_session):
    card, rule, purchase_id = _flagged_purchase(client, db_session)

    response = client.post(f"/entries/{purchase_id}/reward-reprice", json={"keep": True})

    assert response.status_code == 200, response.text
    assert (response.json()["flagged"], response.json()["applied"]) == (False, [])
    accrual = _one(db_session, source_entry_id=purchase_id)
    assert (accrual.amount, _reward_row(db_session, accrual).amount) == (Decimal("10.0000"), Decimal("10.0000"))
    assert _entry(db_session, purchase_id).needs_review is False


def test_reprice_respects_the_window_cap_and_re_allots_later_purchases(client, db_session):
    """Cap 15: the edited first purchase (now 2,000 → 20) gets the whole cap; a later estimated purchase gets 0."""
    card, rule, purchase_id = _flagged_purchase(client, db_session, total_cap=Decimal("15"))
    later = client.post("/entries", json=_payload(card, amount="500", entry_date="2026-11-25",
                                                  reward_rule_ids=[rule.id])).json()["id"]
    assert _one(db_session, source_entry_id=later).amount == Decimal("5.0000")  # cap 15 − frozen 10

    preview = client.get(f"/entries/{purchase_id}/reward-reprice").json()
    assert Decimal(preview["items"][0]["new"]) == Decimal("15")

    assert client.post(f"/entries/{purchase_id}/reward-reprice", json={}).status_code == 200
    assert _one(db_session, source_entry_id=purchase_id).amount == Decimal("15.0000")
    assert _one(db_session, source_entry_id=later).amount == Decimal("0.0000")


def test_two_rules_sharing_a_cap_are_repriced_together_within_it(client, db_session):
    """Shared cap 30: booked 10 + 20 at 1,000; at 2,000 the estimates are 20 + 40, allotted in rule order 20 + 10."""
    card = _card(db_session)
    group = uuid4()
    rule_a = _rule(db_session, card, name="測試規則A", total_cap=Decimal("30"), shared_cap_id=group)
    rule_b = _rule(db_session, card, name="測試規則B", rate=Decimal("2"), total_cap=Decimal("30"), shared_cap_id=group)
    db_session.commit()
    links = [rule_a.id, rule_b.id]
    purchase_id = client.post("/entries", json=_payload(card, reward_rule_ids=links)).json()["id"]
    for rule in (rule_a, rule_b):
        _post(db_session, _one(db_session, source_entry_id=purchase_id, rule_id=rule.id))
    db_session.commit()
    assert client.put(f"/entries/{purchase_id}", json=_payload(card, amount="2000", reward_rule_ids=links)).status_code == 200

    preview = client.get(f"/entries/{purchase_id}/reward-reprice").json()
    assert {i["rule_id"]: (Decimal(i["booked"]), Decimal(i["new"]), i["status"]) for i in preview["items"]} == {
        rule_a.id: (Decimal("10"), Decimal("20"), "reprice"), rule_b.id: (Decimal("20"), Decimal("10"), "reprice")}

    assert client.post(f"/entries/{purchase_id}/reward-reprice", json={}).status_code == 200
    amounts = {rule.id: _one(db_session, source_entry_id=purchase_id, rule_id=rule.id).amount for rule in (rule_a, rule_b)}
    assert amounts == {rule_a.id: Decimal("20.0000"), rule_b.id: Decimal("10.0000")}


def test_a_zero_allotment_is_not_written_and_keeps_the_flag(client, db_session):
    """Two purchases booked at 10 each under cap 20; the cap is then cut to 10, so the other purchase's frozen 10
    takes it all and the edited one would get 0: not written, left for the owner."""
    card, rule, purchase_id = _flagged_purchase(client, db_session, total_cap=Decimal("20"))
    other = client.post("/entries", json=_payload(card, reward_rule_ids=[rule.id])).json()["id"]
    _post(db_session, _one(db_session, source_entry_id=other))
    rule.total_cap = Decimal("10")
    db_session.commit()

    body = client.post(f"/entries/{purchase_id}/reward-reprice", json={}).json()

    assert [(i["status"], Decimal(i["new"])) for i in body["items"]] == [("zero", Decimal("0"))]
    assert (body["flagged"], body["applied"]) == (True, [])
    assert _one(db_session, source_entry_id=purchase_id).amount == Decimal("10.0000")


def test_an_owner_edited_reward_row_is_not_repriced(client, db_session):
    card, rule, purchase_id = _flagged_purchase(client, db_session)
    accrual = _one(db_session, source_entry_id=purchase_id)
    reward = _reward_row(db_session, accrual)
    reward.amount = Decimal("12")
    db_session.commit()

    body = client.post(f"/entries/{purchase_id}/reward-reprice", json={}).json()

    assert [(i["status"], i["new"]) for i in body["items"]] == [("row_edited", None)]
    assert body["applied"] == [] and body["flagged"] is False
    assert _reward_row(db_session, accrual).amount == Decimal("12.0000")


def test_a_bank_confirmed_reward_is_listed_and_left_alone(client, db_session):
    card, rule, purchase_id = _flagged_purchase(client, db_session)
    accrual = _one(db_session, source_entry_id=purchase_id)
    accrual.state = "bank_confirmed"
    db_session.commit()

    body = client.post(f"/entries/{purchase_id}/reward-reprice", json={}).json()

    assert [i["status"] for i in body["items"]] == ["confirmed"] and body["applied"] == []
    assert _one(db_session, source_entry_id=purchase_id).amount == Decimal("10.0000")


def test_an_unlinked_rule_is_not_repriced(client, db_session):
    card, rule, purchase_id = _flagged_purchase(client, db_session)
    other = _rule(db_session, card, name="另一條")
    db_session.commit()
    assert client.put(f"/entries/{purchase_id}/reward-rules", json={"reward_rule_ids": [other.id]}).status_code == 200

    items = client.get(f"/entries/{purchase_id}/reward-reprice").json()["items"]
    assert [(i["rule_id"], i["status"], i["new"]) for i in items] == [(rule.id, "rule_unlinked", None)]

    # A reprice leaves it for the owner: the flag stays until 保持原樣 (or the reward row is edited or deleted).
    assert client.post(f"/entries/{purchase_id}/reward-reprice", json={}).json()["flagged"] is True
    assert _entry(db_session, purchase_id).review_reason == "reward_posted"
    assert client.post(f"/entries/{purchase_id}/reward-reprice", json={"keep": True}).json()["flagged"] is False
    assert _entry(db_session, purchase_id).needs_review is False


def test_a_purchase_moved_to_another_window_is_not_repriced(client, db_session):
    card, rule, purchase_id = _flagged_purchase(client, db_session)
    assert client.put(f"/entries/{purchase_id}", json=_payload(card, amount="2000", entry_date="2026-12-20",
                                                                reward_rule_ids=[rule.id])).status_code == 200

    (item,) = client.get(f"/entries/{purchase_id}/reward-reprice").json()["items"]
    assert (item["status"], item["new"]) == ("window_moved", None)

    body = client.post(f"/entries/{purchase_id}/reward-reprice", json={}).json()
    assert (body["flagged"], body["applied"]) == (True, [])
    assert _one(db_session, source_entry_id=purchase_id).amount == Decimal("10.0000")


def test_an_unflagged_purchase_previews_as_same(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    db_session.commit()
    purchase_id = client.post("/entries", json=_payload(card, reward_rule_ids=[rule.id])).json()["id"]
    _post(db_session, _one(db_session, source_entry_id=purchase_id))
    db_session.commit()

    body = client.get(f"/entries/{purchase_id}/reward-reprice").json()

    assert body["flagged"] is False and [i["status"] for i in body["items"]] == ["same"]


def test_an_unknown_entry_is_404(client):
    assert client.get("/entries/999999/reward-reprice").status_code == 404
    assert client.post("/entries/999999/reward-reprice", json={}).status_code == 404
