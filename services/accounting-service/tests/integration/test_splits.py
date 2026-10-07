"""Splits: one entry_group of kind split; upsert by member id, protected members, defaults from the group."""

import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import EntryGroup, EntryRewardRule, LedgerEntry
from app.schemas.writes import SettleIn, SplitIn
from app.services import entry_write_service as ews
from app.services import ledger_service
from app.services import settlement_service as st
from app.services import split_service as ss
from app.services.edit_lock import EditLockedError
from app.services.errors import RETRY, CodedConflictError, NotFoundError, ValidationError
from tests.helpers import PROTECTED_REASONS, make_protected_member, race

DAY = date(2026, 9, 1)


def _member(account, **fields) -> dict:
    body = {"account_id": account.id, "kind": "expense", "amount": "100"}
    body.update(fields)
    return body


def _split(*members, **fields) -> dict:
    body = {"entry_date": DAY.isoformat(), "members": list(members)}
    body.update(fields)
    return body


def _members(db, group_id) -> list[LedgerEntry]:
    db.expire_all()
    return list(
        db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
            .order_by(LedgerEntry.seq)
        )
    )


def _balance(db, account_id) -> Decimal:
    db.expire_all()
    return ledger_service.account_balance(db, account_id)


def _keep(entry_id: int, **fields) -> dict:
    return {"id": entry_id, "keep": True, **fields}


def _echo(entry: LedgerEntry, **fields) -> dict:
    """A full member re-sending the stored row the way the rework's client does: every per-member field explicit.
    Fee / discount / rule ids / FX inputs are passed in `fields` when the row has them."""
    body = {
        "id": entry.id, "account_id": entry.account_id, "kind": entry.kind, "amount": str(abs(entry.amount)),
        "entry_date": entry.entry_date.isoformat(),
        "entry_time": entry.entry_time.isoformat() if entry.entry_time else None,
        "posted_date": entry.posted_date.isoformat(), "category_id": entry.category_id,
        "project_id": entry.project_id, "name": entry.name, "merchant": entry.merchant,
        "counterparty_id": entry.counterparty_id, "description": entry.description, "tags": list(entry.tags),
    }
    body.update(fields)
    return body


def _ledger(db) -> dict[int, tuple]:
    """Every row's money-relevant columns (name and tags left out on purpose)."""
    db.expire_all()
    return {
        e.id: (e.amount, e.kind, e.account_id, e.is_settlement, e.settles_entry_id, e.refunds_entry_id,
               e.transfer_group_id, e.reward_source_entry_id, e.entry_date, e.entry_time, e.posted_date, e.group_id)
        for e in db.scalars(select(LedgerEntry))
    }


def _spy_prepare(monkeypatch) -> list:
    """Record the amount of every payload split_service prepares."""
    calls = []
    original = ss.prepare_entry

    def spy(db, payload, **kwargs):
        calls.append(payload.amount)
        return original(db, payload, **kwargs)

    monkeypatch.setattr(ss, "prepare_entry", spy)
    return calls


def test_friend_paid_for_lunch(client, db_session, seed):
    # Spec: "Friend paid for lunch".
    a = seed.account("A", opening="1000")
    alan = seed.counterparty("Alan")
    breakfast = seed.category("早餐")
    db_session.commit()
    a_id = a.id

    response = client.post(
        "/splits",
        json=_split(
            _member(a, kind="payable", name="借入", counterparty_id=alan.id),
            _member(a, kind="expense", name="早餐", category_id=breakfast.id),
            name="早餐 Alan 先付",
        ),
    )

    assert response.status_code == 201
    body = response.json()
    group = db_session.get(EntryGroup, body["group_id"])
    members = _members(db_session, group.id)
    assert (group.kind, group.name) == ("split", "早餐 Alan 先付")
    assert [m.id for m in members] == body["member_ids"]
    assert [(m.kind, m.amount) for m in members] == [("payable", Decimal("100")), ("expense", Decimal("-100"))]
    assert all(m.source == "manual" for m in members)
    assert _balance(db_session, a_id) == Decimal("1000")


def test_split_with_two_accounts(db_session, seed):
    # Spec: "Split with two accounts".
    a, b = seed.account("A"), seed.account("B")
    alan = seed.counterparty("Alan")

    group_id = ss.create_split(
        db_session,
        SplitIn(**_split(
            _member(a, amount="230", name="午餐"),
            _member(b, kind="receivable", amount="180", name="代付", counterparty_id=alan.id),
        )),
    )
    db_session.flush()

    members = _members(db_session, group_id)
    assert [(m.account_id, m.kind, m.amount) for m in members] == [
        (a.id, "expense", Decimal("-230")), (b.id, "receivable", Decimal("-180")),
    ]
    assert (_balance(db_session, a.id), _balance(db_session, b.id)) == (Decimal("-230"), Decimal("-180"))


def test_members_default_to_the_group_fields_unless_they_override(db_session, seed):
    wallet = seed.account()
    trip, life = seed.project("日本行"), seed.project("生活")

    group_id = ss.create_split(
        db_session,
        SplitIn(**_split(
            _member(wallet, fee={"amount": "5"}),
            _member(wallet, entry_date="2026-09-02", project_id=life.id, tags=["自己"]),
            entry_time="19:30", posted_date="2026-09-03", project_id=trip.id, tags=["旅行"],
        )),
    )
    db_session.flush()

    first, second = _members(db_session, group_id)
    assert (first.entry_date, first.entry_time, first.posted_date, first.project_id, first.tags) == (
        DAY, time(19, 30), date(2026, 9, 3), trip.id, ["旅行"],
    )
    assert (second.entry_date, second.entry_time, second.posted_date, second.project_id, second.tags) == (
        date(2026, 9, 2), time(19, 30), date(2026, 9, 2), life.id, ["自己"],
    )  # a member that overrides entry_date posts on its own date, not the group's posted_date
    [fee] = db_session.scalars(select(LedgerEntry).where(LedgerEntry.parent_entry_id == first.id)).all()
    assert (fee.amount, fee.group_id, fee.posted_date) == (Decimal("-5"), None, date(2026, 9, 3))


def test_member_errors_name_the_member_and_write_nothing(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()

    response = client.post("/splits", json=_split(_member(wallet), _member(wallet, kind="receivable")))
    empty = client.post("/splits", json=_split())

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["members.1.counterparty_id"]]
    assert empty.status_code == 422
    db_session.expire_all()
    assert db_session.scalars(select(EntryGroup)).all() == []
    assert db_session.scalars(select(LedgerEntry)).all() == []


def test_update_with_only_new_members_replaces_the_old_ones(client, db_session, seed):
    wallet, card = seed.account("錢包"), seed.account("卡")
    group_id = ss.create_split(
        db_session, SplitIn(**_split(_member(wallet, fee={"amount": "5"}), _member(wallet), name="舊"))
    )
    db_session.commit()
    old_ids = [m.id for m in _members(db_session, group_id)]
    wallet_id, card_id = wallet.id, card.id

    response = client.put(
        f"/splits/{group_id}",
        json=_split(_member(card, amount="70"), _member(card, amount="30"), name="新", merchant="全家",
                    entry_date="2026-09-05"),
    )

    assert response.status_code == 200, response.text
    members = _members(db_session, group_id)
    assert response.json() == {
        "group_id": group_id, "member_ids": [m.id for m in members],
        "members": [{"id": m.id, "client_key": None} for m in members],
    }
    assert not set(old_ids) & {m.id for m in members}
    assert [(m.account_id, m.amount, m.entry_date) for m in members] == [
        (card_id, Decimal("-70"), date(2026, 9, 5)), (card_id, Decimal("-30"), date(2026, 9, 5)),
    ]
    group = db_session.get(EntryGroup, group_id)
    assert (group.name, group.merchant) == ("新", "全家")
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == wallet_id)).all() == []


def test_delete_removes_group_members_and_children(client, db_session, seed):
    wallet = seed.account()
    group_id = ss.create_split(
        db_session, SplitIn(**_split(_member(wallet, fee={"amount": "5"}, discount={"amount": "3"}), _member(wallet)))
    )
    db_session.commit()

    assert client.delete(f"/splits/{group_id}").status_code == 204

    db_session.expire_all()
    assert db_session.get(EntryGroup, group_id) is None
    assert db_session.scalars(select(LedgerEntry)).all() == []
    assert client.delete(f"/splits/{group_id}").status_code == 404


def test_split_update_refuses_locked_group(client, db_session, seed, monkeypatch):
    # Review Focus 2: the lock runs on the group and on every member, not only on the group row.
    wallet = seed.account()
    tagged = seed.group(moze_id="pkg-1")
    seed.entry(wallet, "-50", group_id=tagged.id)
    hidden = seed.group()
    manual_member = seed.entry(wallet, "-30", group_id=hidden.id)
    seed.entry(wallet, "-20", group_id=hidden.id, source="moze_backup", moze_id="rec-7")
    db_session.commit()
    tagged_id, hidden_id, manual_member_id = tagged.id, hidden.id, manual_member.id
    body = _split(_member(wallet, amount="10"))

    for group_id in (tagged_id, hidden_id):
        put = client.put(f"/splits/{group_id}", json=body)
        delete = client.delete(f"/splits/{group_id}")
        assert (put.status_code, put.json()["message"]) == (409, "locked_until_cutover")
        assert (delete.status_code, delete.json()["message"]) == (409, "locked_until_cutover")
        with pytest.raises(EditLockedError):
            ss.update_split(db_session, group_id, SplitIn(**body))
    member_put = client.put(
        f"/entries/{manual_member_id}",
        json={"account_id": wallet.id, "kind": "expense", "amount": "35", "entry_date": DAY.isoformat()},
    )
    assert member_put.status_code == 409
    assert len(_members(db_session, hidden_id)) == 2

    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    two = _split(_member(wallet, amount="10"), _member(wallet, amount="5"))
    assert client.put(f"/splits/{hidden_id}", json=two).status_code == 200
    assert [(m.amount, m.source, m.moze_id) for m in _members(db_session, hidden_id)] == [
        (Decimal("-10"), "manual", None), (Decimal("-5"), "manual", None),
    ]


def test_update_split_refuses_group_with_settlement_member(client, db_session, seed, monkeypatch):
    # Plan review round 3: rebuilding members would re-sign the +200 collection as a −200 receivable.
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")  # the cutover lock is not what refuses here
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    group = seed.group()
    seed.entry(wallet, "-200", group_id=group.id)
    collection = seed.entry(
        wallet, "200", kind="receivable", counterparty_id=alan.id, is_settlement=True, group_id=group.id
    )
    db_session.commit()
    group_id, collection_id = group.id, collection.id

    response = client.put(f"/splits/{group_id}", json=_split(_member(wallet, amount="10"), _member(wallet, amount="5")))

    assert response.status_code == 409  # the protected member would be dropped
    assert response.json()["message"].startswith("member_locked")
    db_session.expire_all()
    stored = db_session.get(LedgerEntry, collection_id)
    assert (stored.amount, stored.is_settlement, stored.group_id) == (Decimal("200"), True, group_id)
    assert len(_members(db_session, group_id)) == 2


def test_update_split_refuses_group_with_settled_member(client, db_session, seed, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    group = seed.group()
    lent = seed.entry(wallet, "-200", kind="receivable", counterparty_id=alan.id, group_id=group.id)
    db_session.flush()
    seed.entry(wallet, "200", kind="receivable", counterparty_id=alan.id, settles_entry_id=lent.id, is_settlement=True)
    db_session.commit()
    group_id, lent_id = group.id, lent.id

    response = client.put(f"/splits/{group_id}", json=_split(_member(wallet, amount="10"), _member(wallet, amount="5")))

    assert response.status_code == 409  # the protected member would be dropped
    assert response.json()["message"].startswith("member_locked")
    [member] = _members(db_session, group_id)
    assert (member.id, member.amount) == (lent_id, Decimal("-200"))


def test_update_split_refuses_group_with_transfer_leg(client, db_session, seed, monkeypatch):
    # Fix round 1: an imported split package may hold one leg of a transfer whose counterpart is outside the
    # group; rebuilding the group would orphan the counterpart.
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    a, b = seed.account("A"), seed.account("B")
    group = seed.group()
    pair = uuid.uuid4()
    out_leg = seed.entry(a, "-100", kind="transfer_out", transfer_group_id=pair, group_id=group.id)
    in_leg = seed.entry(b, "100", kind="transfer_in", transfer_group_id=pair)
    db_session.commit()
    group_id, leg_ids = group.id, {out_leg.id, in_leg.id}

    response = client.put(f"/splits/{group_id}", json=_split(_member(a, amount="10"), _member(a, amount="5")))

    assert response.status_code == 409  # the protected member would be dropped
    assert response.json()["message"].startswith("member_locked")
    db_session.expire_all()
    assert {e.id for e in db_session.scalars(select(LedgerEntry).where(LedgerEntry.transfer_group_id == pair))} == leg_ids


def test_delete_split_refuses_group_with_reward_member(client, db_session, seed, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    wallet = seed.account()
    group = seed.group()
    seed.entry(wallet, "-100", group_id=group.id)
    reward = seed.entry(wallet, "3", kind="reward", group_id=group.id)
    db_session.commit()
    group_id, reward_id = group.id, reward.id

    response = client.delete(f"/splits/{group_id}")

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["members"]]
    db_session.expire_all()
    assert db_session.get(LedgerEntry, reward_id) is not None
    assert len(_members(db_session, group_id)) == 2


def test_concurrent_split_updates_serialise_and_the_second_retries(pg_engine, db_session, seed):
    # Plan review round 4 P1 (never both amounts) under upsert: both PUTs change one member; the group row lock
    # makes the second wait, and its re-read inside the lock finds the member changed under it → 409 retry.
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)

    def put(amount):
        body = _split({"id": first_id, "account_id": wallet_id, "kind": "expense", "amount": amount}, _keep(second_id))
        return lambda db: ss.update_split(db, group_id, SplitIn(**body))

    result = race(pg_engine, put("200"), put("300"))

    assert isinstance(result, CodedConflictError) and result.code == RETRY
    assert [m.amount for m in _members(db_session, group_id)] == [Decimal("-200"), Decimal("-50")]
    assert _balance(db_session, wallet_id) == Decimal("750")


def test_delete_split_racing_update_leaves_one_outcome(pg_engine, db_session, seed):
    # The PUT holds the group lock; DELETE /splits waits on it, then sees and removes the PUT's members (same ids).
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    body = _split({"id": first_id, "account_id": wallet_id, "kind": "expense", "amount": "300"}, _keep(second_id))

    result = race(
        pg_engine,
        lambda db: ss.update_split(db, group_id, SplitIn(**body)),
        lambda db: ss.delete_split(db, group_id),
    )

    assert result == "committed"
    db_session.expire_all()
    assert db_session.get(EntryGroup, group_id) is None
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == wallet_id)).all() == []
    assert _balance(db_session, wallet_id) == Decimal("1000")


def _two_member_split(db_session, seed):
    wallet = seed.account("A", opening="1000")
    group_id = ss.create_split(db_session, SplitIn(**_split(_member(wallet, amount="100"), _member(wallet, amount="50"))))
    db_session.commit()
    wallet_id = wallet.id
    assert _balance(db_session, wallet_id) == Decimal("850")
    return wallet_id, group_id, ss.member_ids(db_session, group_id)


def _assert_consistent(db_session, wallet_id, group_id) -> list[LedgerEntry]:
    """Every entry left on the account belongs to the (still existing) group, and the balance matches them."""
    db_session.expire_all()
    remaining = db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == wallet_id)).all()
    if remaining:
        assert db_session.get(EntryGroup, group_id) is not None
        assert {m.group_id for m in remaining} == {group_id}
    assert _balance(db_session, wallet_id) == Decimal("1000") + sum((m.amount for m in remaining), Decimal("0"))
    return remaining


def _put_dropping_first(wallet_id, second_id) -> dict:
    return _split(
        {"id": second_id, "account_id": wallet_id, "kind": "expense", "amount": "300"},
        {"account_id": wallet_id, "kind": "expense", "amount": "20"},
    )


def test_single_delete_racing_split_update_does_not_deadlock(pg_engine, db_session, seed):
    # Plan review round 5 P2: delete_entry takes lock_group first, so it waits on the PUT's group lock; the PUT
    # dropped the member, so the delete then finds it gone (404), never a deadlock.
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    body = _put_dropping_first(wallet_id, second_id)

    result = race(
        pg_engine,
        lambda db: ss.update_split(db, group_id, SplitIn(**body)),
        lambda db: ews.delete_entry(db, first_id),
    )

    assert not isinstance(result, OperationalError)  # no deadlock / lock error
    assert isinstance(result, NotFoundError)
    remaining = _assert_consistent(db_session, wallet_id, group_id)
    assert sorted(m.amount for m in remaining) == [Decimal("-300"), Decimal("-20")]
    assert second_id in [m.id for m in remaining]  # kept in place, not recreated


def test_split_update_racing_single_delete_does_not_deadlock(pg_engine, db_session, seed):
    # The reverse order: the single DELETE holds the group lock (proved with NOWAIT from a third connection), the
    # PUT waits on it; the membership it classified is gone under the lock → 409 retry, nothing half-written.
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    body = _put_dropping_first(wallet_id, second_id)

    def delete_and_prove_group_lock(db):
        ews.delete_entry(db, first_id)
        with pg_engine.connect() as probe:
            with pytest.raises(OperationalError):  # LockNotAvailable: the group row is held by this delete
                probe.execute(select(EntryGroup.id).where(EntryGroup.id == group_id).with_for_update(nowait=True))
            probe.rollback()

    result = race(pg_engine, delete_and_prove_group_lock, lambda db: ss.update_split(db, group_id, SplitIn(**body)))

    assert not isinstance(result, OperationalError)  # no deadlock / lock error
    assert isinstance(result, CodedConflictError) and result.code == RETRY
    db_session.expire_all()
    [survivor] = db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == wallet_id)).all()
    assert (survivor.id, survivor.amount) == (second_id, Decimal("-50"))
    assert _balance(db_session, wallet_id) == Decimal("950")


def test_member_payloads_keep_explicit_overrides():
    payload = SplitIn(**_split({"account_id": 1, "kind": "income", "amount": "1", "tags": []}, tags=["群組"]))
    [member] = ss.member_payloads(payload)
    assert (member.entry_date, member.tags) == (DAY, [])


def test_unknown_split_is_404(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()
    assert client.put("/splits/999999", json=_split(_member(wallet))).status_code == 404
    with pytest.raises(ValidationError):
        ss.create_split(db_session, SplitIn(**_split(_member(wallet, account_id=999999))))


def test_split_category_defaults_are_written_in_category_id_order(db_session, seed):
    """Two concurrent splits touching the same categories in opposite member order must not deadlock on the
    category rows, so the default updates run in ascending category id order, after every member is written."""
    from sqlalchemy import event

    wallet, card = seed.account("錢包"), seed.account("卡")
    first, second = seed.category("A"), seed.category("B")
    db_session.commit()
    updated: list[int] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE category"):
            rows = parameters if executemany else [parameters]
            updated.extend(row["category_id"] if "category_id" in row else row["id_1"] for row in rows)

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        ss.create_split(db_session, SplitIn(**_split(
            _member(card, category_id=second.id), _member(wallet, category_id=first.id),
            _member(wallet, category_id=second.id),
        )))
        db_session.flush()
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)

    assert updated == [first.id, second.id]
    db_session.expire_all()
    assert (first.default_account_id, second.default_account_id) == (wallet.id, wallet.id)  # last member wins


def test_split_group_merchant_and_description_round_trip_through_entry_detail(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()
    created = client.post("/splits", json=_split(
        _member(wallet), _member(wallet, amount="50"), name="聚餐", merchant="鼎泰豐", description="生日",
    ))
    assert created.status_code == 201, created.text
    member_id = created.json()["member_ids"][0]

    group = client.get(f"/entries/{member_id}").json()["group"]

    assert (group["name"], group["merchant"], group["description"], group["count"]) == ("聚餐", "鼎泰豐", "生日", 2)


def test_post_needs_two_new_members(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()

    single = client.post("/splits", json=_split(_member(wallet)))
    with_id = client.post("/splits", json=_split(_member(wallet, id=1), _member(wallet)))
    with_keep = client.post("/splits", json=_split(_member(wallet), {"id": 1, "keep": True}))
    too_many = client.post("/splits", json=_split(*[_member(wallet) for _ in range(51)]))

    assert [(r.status_code, r.json()["detail"][0]["loc"]) for r in (single, with_id, with_keep, too_many)] == [
        (422, ["members"]), (422, ["members.0.id"]), (422, ["members.1.keep"]), (422, ["members"]),
    ]
    db_session.expire_all()
    assert db_session.scalars(select(EntryGroup)).all() == []
    assert db_session.scalars(select(LedgerEntry)).all() == []


def test_post_echoes_client_keys_in_request_order(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()

    response = client.post(
        "/splits", json=_split(_member(wallet, client_key="k-a"), _member(wallet, amount="50", client_key="k-b"))
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"group_id", "member_ids", "members"}
    assert body["members"] == [
        {"id": body["member_ids"][0], "client_key": "k-a"}, {"id": body["member_ids"][1], "client_key": "k-b"},
    ]
    assert [m.id for m in _members(db_session, body["group_id"])] == body["member_ids"]


def test_upsert_keeps_ids_and_applies_keep_full_keep_meta_new_and_drop(client, db_session, seed):
    wallet = seed.account("錢包", opening="1000")
    db_session.commit()
    group_id = ss.create_split(db_session, SplitIn(**_split(
        _member(wallet, name="午餐"), _member(wallet, amount="50", name="飲料"), _member(wallet, amount="30", name="甜點"),
    )))
    db_session.commit()
    lunch, drink, dessert = _members(db_session, group_id)
    lunch_id, drink_id, dessert_id, wallet_id = lunch.id, drink.id, dessert.id, wallet.id

    response = client.put(f"/splits/{group_id}", json=_split(
        _member(wallet, amount="40", name="點心", client_key="new"),
        _echo(lunch, amount="120", client_key="lunch"),
        _keep(drink_id, name="珍奶", tags=["手搖"]),
        name="聚餐",
    ))

    assert response.status_code == 200, response.text
    body = response.json()
    new_id = body["member_ids"][0]
    assert body == {"group_id": group_id, "member_ids": [new_id, lunch_id, drink_id], "members": [
        {"id": new_id, "client_key": "new"}, {"id": lunch_id, "client_key": "lunch"},
        {"id": drink_id, "client_key": None},
    ]}
    rows = {m.id: m for m in _members(db_session, group_id)}
    assert set(rows) == {lunch_id, drink_id, new_id}  # dessert dropped, nobody recreated
    assert (rows[lunch_id].amount, rows[lunch_id].name) == (Decimal("-120"), "午餐")
    assert (rows[drink_id].amount, rows[drink_id].name, rows[drink_id].tags) == (Decimal("-50"), "珍奶", ["手搖"])
    assert (rows[new_id].amount, rows[new_id].name) == (Decimal("-40"), "點心")
    assert db_session.get(LedgerEntry, dessert_id) is None
    assert db_session.get(EntryGroup, group_id).name == "聚餐"
    assert _balance(db_session, wallet_id) == Decimal("1000") - 120 - 50 - 40


def test_unknown_or_foreign_member_id_is_404_and_writes_nothing(client, db_session, seed):
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    other = ss.create_split(db_session, SplitIn(**_split(
        {"account_id": wallet_id, "kind": "expense", "amount": "1"}, {"account_id": wallet_id, "kind": "expense", "amount": "2"},
    )))
    db_session.commit()
    foreign_id = ss.member_ids(db_session, other)[0]

    for unknown in (foreign_id, 999999):
        response = client.put(f"/splits/{group_id}", json=_split(_keep(first_id, name="改名"), _keep(unknown)))
        assert response.status_code == 404
        assert response.json()["message"].startswith("member_not_found")
    assert [m.name for m in _members(db_session, group_id)] == [None, None]


def test_one_member_put_needs_an_existing_member(client, db_session, seed):
    wallet_id, group_id, _ = _two_member_split(db_session, seed)

    response = client.put(f"/splits/{group_id}", json=_split({"account_id": wallet_id, "kind": "expense", "amount": "5"}))

    assert (response.status_code, [e["loc"] for e in response.json()["detail"]]) == (422, [["members.0.id"]])
    assert len(_members(db_session, group_id)) == 2


@pytest.mark.parametrize("case", sorted(PROTECTED_REASONS))
def test_protected_member_takes_metadata_only(client, db_session, seed, case):
    # Review Focus 3: keep + another member dropped → 200; the same member as full, or dropped → 409, nothing written.
    wallet = seed.account("錢包", opening="1000")
    group = seed.group(name="聚餐")
    plain = seed.entry(wallet, "-100", group_id=group.id)
    extra = seed.entry(wallet, "-40", group_id=group.id)
    member = make_protected_member(seed, case, wallet, group.id)
    db_session.commit()
    group_id, plain_id, extra_id, member_id, wallet_id = group.id, plain.id, extra.id, member.id, wallet.id
    plain_echo = _echo(plain, amount="120")
    as_full = {"id": member_id, "account_id": wallet_id, "kind": "expense", "amount": "1"}
    before = _ledger(db_session)

    full = client.put(f"/splits/{group_id}", json=_split(plain_echo, _keep(extra_id), as_full))
    dropped = client.put(f"/splits/{group_id}", json=_split(plain_echo, _keep(extra_id)))

    assert [(r.status_code, r.json()["message"].split(":")[0]) for r in (full, dropped)] == [(409, "member_locked")] * 2
    assert PROTECTED_REASONS[case] in full.json()["message"]
    assert _ledger(db_session) == before

    kept = client.put(f"/splits/{group_id}", json=_split(plain_echo, _keep(member_id, name="改名", tags=["保護"])))

    assert kept.status_code == 200, kept.text
    after = _ledger(db_session)
    assert extra_id not in after and after[plain_id][0] == Decimal("-120")
    untouched = {key: value for key, value in before.items() if key not in (plain_id, extra_id)}
    assert {key: value for key, value in after.items() if key != plain_id} == untouched
    stored = db_session.get(LedgerEntry, member_id)
    assert (stored.name, stored.tags) == ("改名", ["保護"])


def test_imported_transfer_leg_keeps_its_counterpart_after_cutover(client, db_session, seed, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    a, b = seed.account("A"), seed.account("B")
    group = seed.group(moze_id="pkg-9")
    pair = uuid.uuid4()
    imported = {"source": "moze_backup"}
    out_leg = seed.entry(a, "-100", kind="transfer_out", transfer_group_id=pair, group_id=group.id, moze_id="rec-91", **imported)
    in_leg = seed.entry(b, "100", kind="transfer_in", transfer_group_id=pair, moze_id="rec-92", **imported)
    meal = seed.entry(a, "-30", group_id=group.id, moze_id="rec-93", **imported)
    db_session.commit()
    group_id, out_id, in_id, meal_id = group.id, out_leg.id, in_leg.id, meal.id

    response = client.put(f"/splits/{group_id}", json=_split(_keep(out_id, name="轉給B"), _echo(meal, amount="35")))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    out_row, in_row, meal_row = (db_session.get(LedgerEntry, i) for i in (out_id, in_id, meal_id))
    assert (out_row.amount, out_row.transfer_group_id, out_row.name, out_row.source) == (Decimal("-100"), pair, "轉給B", "moze_backup")
    assert (in_row.amount, in_row.transfer_group_id) == (Decimal("100"), pair)
    assert (meal_row.amount, meal_row.source) == (Decimal("-35"), "manual")


def test_referenced_loan_cannot_be_dropped_or_change_kind(client, db_session, seed):
    wallet = seed.account()
    group = seed.group()
    plain = seed.entry(wallet, "-100", group_id=group.id)
    loan = make_protected_member(seed, "scheduled_loan", wallet, group.id)
    db_session.commit()
    group_id, wallet_id, loan_id, party_id = group.id, wallet.id, loan.id, loan.counterparty_id
    as_receivable = {"id": loan_id, "account_id": wallet_id, "kind": "receivable", "amount": "1000", "counterparty_id": party_id}
    before = _ledger(db_session)

    for body in (_split(_echo(plain), _member(wallet, amount="5")), _split(_echo(plain), as_receivable)):
        response = client.put(f"/splits/{group_id}", json=body)
        assert response.status_code == 409
        assert response.json()["message"].startswith("member_locked") and "scheduled_loan" in response.json()["message"]
    assert _ledger(db_session) == before


def test_attached_disabled_rule_round_trips_on_keep_full_but_not_on_new(client, db_session, seed):
    card = seed.account("卡")
    rule = seed.rule(card, "一般回饋")
    db_session.commit()
    group_id = ss.create_split(db_session, SplitIn(**_split(_member(card, reward_rule_ids=[rule.id]), _member(card, amount="50"))))
    rule.is_enabled = False  # an import disabled it after it was attached
    db_session.commit()
    first, second = _members(db_session, group_id)
    rule_id, card_id, first_id = rule.id, card.id, first.id
    first_body, second_body = _echo(first, amount="120", reward_rule_ids=[rule_id]), _echo(second)

    kept = client.put(f"/splits/{group_id}", json=_split(first_body, second_body))
    refused = client.put(f"/splits/{group_id}", json=_split(
        first_body, second_body, {"account_id": card_id, "kind": "expense", "amount": "10", "reward_rule_ids": [rule_id]},
    ))

    assert kept.status_code == 200, kept.text
    assert (refused.status_code, [e["loc"] for e in refused.json()["detail"]]) == (422, [["members.2.reward_rule_ids"]])
    db_session.expire_all()
    assert db_session.scalars(select(EntryRewardRule.rule_id).where(EntryRewardRule.entry_id == first_id)).all() == [rule_id]
    assert len(_members(db_session, group_id)) == 2


def test_old_split_keeps_each_member_date_when_one_amount_changes(client, db_session, seed):
    # Review Focus 1: an old split whose members have different dates, times and merchants; only the second
    # amount changes → the first is not even prepared, and each member keeps its own date/time/merchant.
    wallet = seed.account()
    group = seed.group(name="舊拆帳")
    first = seed.entry(wallet, "-100", group_id=group.id, day=date(2026, 9, 1), entry_time=time(9, 15, 30), merchant="舊商家")
    second = seed.entry(wallet, "-50", group_id=group.id, day=date(2026, 9, 3), entry_time=time(20, 0))
    db_session.commit()
    group_id, first_id, second_id = group.id, first.id, second.id
    first_updated_at = first.updated_at

    response = client.put(f"/splits/{group_id}", json=_split(_echo(first), _echo(second, amount="60"), name="舊拆帳"))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    a, b = db_session.get(LedgerEntry, first_id), db_session.get(LedgerEntry, second_id)
    assert (a.entry_date, a.entry_time, a.posted_date, a.merchant, a.amount) == (
        date(2026, 9, 1), time(9, 15, 30), date(2026, 9, 1), "舊商家", Decimal("-100"),
    )
    assert a.updated_at == first_updated_at  # fully unchanged: not written at all
    assert (b.entry_date, b.entry_time, b.amount) == (date(2026, 9, 3), time(20, 0), Decimal("-60"))


def test_metadata_only_change_on_an_online_fx_member_makes_no_fx_request(db_session, seed, fake_http):
    # Review Focus 5: name changed only, provider unavailable → no FX request; FX fields and children unchanged.
    card = seed.account("華航卡")
    group = seed.group()
    online = seed.entry(card, "-389.34", group_id=group.id, original_amount=Decimal("-1800"), original_currency="JPY",
                        fx_rate=Decimal("0.2163"), fx_source="fx_api", name="拉麵")
    fee = seed.entry(card, "-15", kind="fee", parent_entry_id=online.id, name="手續費")
    other = seed.entry(card, "-100", group_id=group.id)
    db_session.commit()
    group_id, online_id, fee_id = group.id, online.id, fee.id
    http = fake_http({})  # every request would fail and be recorded
    body = _split(
        _echo(online, amount=None, original_amount="1800", original_currency="JPY", fx_rate=None, name="豚骨拉麵",
              fee={"amount": "15"}),
        _echo(other, amount="120"),
    )

    ss.update_split(db_session, group_id, SplitIn(**body), http_get=http)
    db_session.commit()

    assert http.calls == []
    db_session.expire_all()
    row = db_session.get(LedgerEntry, online_id)
    assert (row.amount, row.original_amount, row.fx_rate, row.fx_source, row.name) == (
        Decimal("-389.34"), Decimal("-1800"), Decimal("0.2163"), "fx_api", "豚骨拉麵",
    )
    assert db_session.get(LedgerEntry, fee_id) is not None  # the child was not rebuilt


def test_an_unchanged_member_with_an_expired_rule_is_not_prepared(client, db_session, seed, monkeypatch):
    card = seed.account("卡")
    rule = seed.rule(card, "限期回饋")
    db_session.commit()
    group_id = ss.create_split(db_session, SplitIn(**_split(_member(card, reward_rule_ids=[rule.id]), _member(card, amount="50"))))
    rule.ends_on = date(2026, 8, 31)  # expired before the entry date
    db_session.commit()
    first, second = _members(db_session, group_id)
    rule_id = rule.id
    prepared = _spy_prepare(monkeypatch)

    response = client.put(f"/splits/{group_id}", json=_split(
        _echo(first, reward_rule_ids=[rule_id], name="改名"), _echo(second, amount="60"),
    ))

    assert response.status_code == 200, response.text
    assert prepared == [Decimal("60")]  # only the changed member went through prepare_entry
    assert [m.name for m in _members(db_session, group_id)] == ["改名", None]


def test_fully_unchanged_payload_is_a_no_op(client, db_session, seed, monkeypatch):
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    first, second = _members(db_session, group_id)
    stamps = (first.updated_at, second.updated_at)
    prepared = _spy_prepare(monkeypatch)

    response = client.put(f"/splits/{group_id}", json=_split(_echo(first), _echo(second)))

    assert response.status_code == 200, response.text
    assert prepared == []
    first, second = _members(db_session, group_id)
    assert (first.updated_at, second.updated_at) == stamps


def test_merchant_only_change_is_applied(client, db_session, seed, monkeypatch):
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    first, second = _members(db_session, group_id)
    prepared = _spy_prepare(monkeypatch)

    response = client.put(f"/splits/{group_id}", json=_split(_echo(first, merchant="全家"), _echo(second)))

    assert response.status_code == 200, response.text
    assert prepared == []
    assert [(m.merchant, m.amount) for m in _members(db_session, group_id)] == [("全家", Decimal("-100")), (None, Decimal("-50"))]


def test_reward_ledger_rows_are_never_touched(client, db_session, seed):
    card = seed.account("卡")
    db_session.commit()
    group_id = ss.create_split(db_session, SplitIn(**_split(_member(card), _member(card, amount="50"))))
    db_session.commit()
    first, second = _members(db_session, group_id)
    reward = seed.entry(card, "3", kind="reward", reward_source_entry_id=first.id)
    db_session.commit()
    reward_id, first_id = reward.id, first.id

    response = client.put(f"/splits/{group_id}", json=_split(_echo(first, amount="150"), _echo(second)))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    row = db_session.get(LedgerEntry, reward_id)
    assert (row.amount, row.reward_source_entry_id) == (Decimal("3"), first_id)


def test_a_parent_date_change_only_reaches_full_members(client, db_session, seed):
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    group = seed.group()
    plain = seed.entry(wallet, "-100", group_id=group.id)
    collection = seed.entry(wallet, "200", kind="receivable", counterparty_id=alan.id, is_settlement=True, group_id=group.id)
    db_session.commit()
    group_id, plain_id, collection_id = group.id, plain.id, collection.id

    response = client.put(f"/splits/{group_id}", json=_split(
        _echo(plain, entry_date="2026-09-05", posted_date="2026-09-05"), _keep(collection_id), entry_date="2026-09-05",
    ))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(LedgerEntry, plain_id).entry_date == date(2026, 9, 5)
    assert db_session.get(LedgerEntry, collection_id).entry_date == DAY
