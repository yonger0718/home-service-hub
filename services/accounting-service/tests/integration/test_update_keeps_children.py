"""#396: an update that would delete a stored fee / discount child is refused (409 `children_unconfirmed`) until the
request carries `?drop_children=true`. One rule for the four routes that reach `sync_children`: PUT /entries/{id},
PUT /transfers/{group_id}, PUT /splits/{group_id} and PUT /entries/{id}/split. Fixtures are fictional."""

import uuid
from datetime import date

from sqlalchemy import func, select

from app.models import EntryGroup, LedgerEntry

DAY = date(2026, 9, 1)


def _entry_body(account, **fields) -> dict:
    body = {"account_id": account.id, "kind": "expense", "amount": "100", "entry_date": DAY.isoformat()}
    body.update(fields)
    return body


def _member(account_id, **fields) -> dict:
    body = {"account_id": account_id, "kind": "expense", "amount": "100"}
    body.update(fields)
    return body


def _split(*members) -> dict:
    return {"entry_date": DAY.isoformat(), "members": list(members)}


def _children(db, parent_id) -> dict[str, LedgerEntry]:
    db.expire_all()
    return {c.kind: c for c in db.scalars(select(LedgerEntry).where(LedgerEntry.parent_entry_id == parent_id))}


def _refused(response) -> str:
    assert response.status_code == 409, response.text
    message = response.json()["message"]
    assert message.startswith("children_unconfirmed: "), message
    return message


def _with_fee(client, seed, db, **fields) -> tuple[dict, int, int]:
    """An expense −100 named 早餐 with a 15 fee → (the account's payload base, entry id, fee id)."""
    wallet = seed.account("A", opening="1000")
    db.commit()
    created = client.post("/entries", json=_entry_body(wallet, name="早餐", fee={"amount": "15"}, **fields))
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]
    base = _entry_body(wallet, **fields)
    return base, entry_id, _children(db, entry_id)["fee"].id


def test_put_without_the_stored_fee_is_refused_until_confirmed(client, db_session, seed):
    base, entry_id, fee_id = _with_fee(client, seed, db_session)

    refused = client.put(f"/entries/{entry_id}", json={**base, "name": "午餐"})

    assert f"entry {fee_id} (fee) of entry {entry_id}" in _refused(refused)
    db_session.expire_all()
    assert db_session.get(LedgerEntry, entry_id).name == "早餐"
    assert db_session.get(LedgerEntry, fee_id) is not None

    confirmed = client.put(f"/entries/{entry_id}?drop_children=true", json={**base, "name": "午餐"})

    assert confirmed.status_code == 200, confirmed.text
    db_session.expire_all()
    assert db_session.get(LedgerEntry, entry_id).name == "午餐"
    assert _children(db_session, entry_id) == {}


def test_put_with_the_fee_sent_back_keeps_its_row(client, db_session, seed):
    base, entry_id, fee_id = _with_fee(client, seed, db_session)

    response = client.put(f"/entries/{entry_id}", json={**base, "name": "午餐", "fee": {"amount": "15"}})

    assert response.status_code == 200, response.text
    assert _children(db_session, entry_id)["fee"].id == fee_id


def test_put_with_another_fee_amount_is_an_edit(client, db_session, seed):
    base, entry_id, fee_id = _with_fee(client, seed, db_session)

    response = client.put(f"/entries/{entry_id}", json={**base, "fee": {"amount": "20"}})

    assert response.status_code == 200, response.text
    fee = _children(db_session, entry_id)["fee"]
    assert (fee.id, str(fee.amount)) == (fee_id, "-20.0000")


def test_put_names_only_the_omitted_kind(client, db_session, seed):
    wallet = seed.account("A", opening="1000")
    db_session.commit()
    created = client.post("/entries", json=_entry_body(wallet, fee={"amount": "15"}, discount={"amount": "5"}))
    entry_id = created.json()["id"]
    children = _children(db_session, entry_id)

    refused = client.put(f"/entries/{entry_id}", json=_entry_body(wallet, fee={"amount": "15"}))

    message = _refused(refused)
    assert f"entry {children['discount'].id} (discount) of entry {entry_id}" in message
    assert "(fee)" not in message
    assert set(_children(db_session, entry_id)) == {"fee", "discount"}


def test_put_without_children_on_a_childless_entry_passes(client, db_session, seed):
    wallet = seed.account("A", opening="1000")
    db_session.commit()
    entry_id = client.post("/entries", json=_entry_body(wallet)).json()["id"]

    response = client.put(f"/entries/{entry_id}", json=_entry_body(wallet, amount="120"))

    assert response.status_code == 200, response.text


def _transfer_legs(db, group_id) -> tuple[LedgerEntry, LedgerEntry]:
    db.expire_all()
    legs = {leg.kind: leg for leg in db.scalars(select(LedgerEntry).where(
        LedgerEntry.transfer_group_id == uuid.UUID(str(group_id)), LedgerEntry.parent_entry_id.is_(None)))}
    return legs["transfer_out"], legs["transfer_in"]


def test_transfer_put_without_the_stored_leg_fee_is_refused(client, db_session, seed):
    a, b = seed.account("A", opening="5000"), seed.account("B")
    db_session.commit()
    body = {"from_account_id": a.id, "to_account_id": b.id, "out_amount": "3000", "entry_date": DAY.isoformat()}
    created = client.post("/transfers", json={**body, "out_fee": {"amount": "10"}})
    assert created.status_code == 201, created.text
    group_id = created.json()["transfer_group_id"]
    out_leg, _ = _transfer_legs(db_session, group_id)
    out_id, fee_id = out_leg.id, _children(db_session, out_leg.id)["fee"].id

    refused = client.put(f"/transfers/{group_id}", json={**body, "out_amount": "2000"})

    assert f"entry {fee_id} (fee) of entry {out_id}" in _refused(refused)
    out_leg, in_leg = _transfer_legs(db_session, group_id)
    assert (str(out_leg.amount), str(in_leg.amount)) == ("-3000.0000", "3000.0000")
    assert db_session.get(LedgerEntry, fee_id) is not None

    confirmed = client.put(f"/transfers/{group_id}?drop_children=true", json={**body, "out_amount": "2000"})

    assert confirmed.status_code == 200, confirmed.text
    out_leg, in_leg = _transfer_legs(db_session, group_id)
    assert (str(out_leg.amount), str(in_leg.amount)) == ("-2000.0000", "2000.0000")
    assert _children(db_session, out_id) == {}


def _split_with_a_fee(client, seed, db, *amounts) -> tuple[int, int, list[int], int]:
    """A split whose first member has a 15 fee → (wallet id, group id, member ids, fee id)."""
    wallet = seed.account("A", opening="1000")
    db.commit()
    first, *rest = amounts
    created = client.post("/splits", json=_split(
        _member(wallet.id, amount=first, fee={"amount": "15"}), *(_member(wallet.id, amount=a) for a in rest)))
    assert created.status_code == 201, created.text
    member_ids = created.json()["member_ids"]
    return wallet.id, created.json()["group_id"], member_ids, _children(db, member_ids[0])["fee"].id


def _amounts(db, ids) -> list[str]:
    db.expire_all()
    return [str(db.get(LedgerEntry, entry_id).amount) for entry_id in ids]


def test_split_put_without_a_member_fee_is_refused(client, db_session, seed):
    wallet_id, group_id, (first, second), fee_id = _split_with_a_fee(client, seed, db_session, "100", "50")

    refused = client.put(f"/splits/{group_id}", json=_split(
        _member(wallet_id, id=first, amount="80"), _member(wallet_id, id=second, amount="70")))

    assert f"entry {fee_id} (fee) of entry {first}" in _refused(refused)
    assert _amounts(db_session, [first, second]) == ["-100.0000", "-50.0000"]
    assert db_session.get(LedgerEntry, fee_id) is not None

    confirmed = client.put(f"/splits/{group_id}?drop_children=true", json=_split(
        _member(wallet_id, id=first, amount="80"), _member(wallet_id, id=second, amount="70")))

    assert confirmed.status_code == 200, confirmed.text
    assert _amounts(db_session, [first, second]) == ["-80.0000", "-70.0000"]
    assert _children(db_session, first) == {}


def test_split_put_omitting_the_fee_of_an_otherwise_unchanged_member_is_refused(client, db_session, seed):
    wallet_id, group_id, (first, second), fee_id = _split_with_a_fee(client, seed, db_session, "100", "50")

    refused = client.put(f"/splits/{group_id}", json=_split(
        _member(wallet_id, id=first, amount="100"), _member(wallet_id, id=second, amount="50")))

    assert f"entry {fee_id} (fee) of entry {first}" in _refused(refused)
    assert db_session.get(LedgerEntry, fee_id) is not None


def test_split_put_removing_a_member_still_deletes_its_fee(client, db_session, seed):
    wallet_id, group_id, (first, second, third), fee_id = _split_with_a_fee(
        client, seed, db_session, "100", "50", "30")

    response = client.put(f"/splits/{group_id}", json=_split(
        _member(wallet_id, id=second, amount="50"), _member(wallet_id, id=third, amount="30")))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(LedgerEntry, first) is None and db_session.get(LedgerEntry, fee_id) is None


def test_convert_to_split_without_the_anchor_fee_is_refused(client, db_session, seed):
    base, entry_id, fee_id = _with_fee(client, seed, db_session)
    wallet_id = base["account_id"]
    groups = db_session.scalar(select(func.count()).select_from(EntryGroup))

    refused = client.put(f"/entries/{entry_id}/split", json=_split(
        _member(wallet_id, id=entry_id, amount="60"), _member(wallet_id, amount="40")))

    assert f"entry {fee_id} (fee) of entry {entry_id}" in _refused(refused)
    db_session.expire_all()
    anchor = db_session.get(LedgerEntry, entry_id)
    assert (anchor.group_id, str(anchor.amount)) == (None, "-100.0000")
    assert db_session.scalar(select(func.count()).select_from(EntryGroup)) == groups
    assert db_session.get(LedgerEntry, fee_id) is not None

    confirmed = client.put(f"/entries/{entry_id}/split?drop_children=true", json=_split(
        _member(wallet_id, id=entry_id, amount="60"), _member(wallet_id, amount="40")))

    assert confirmed.status_code == 200, confirmed.text
    assert _children(db_session, entry_id) == {}
