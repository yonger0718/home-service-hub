"""Splits: one entry_group of kind split; members of mixed kinds and accounts, defaults from the group."""

import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.models import EntryGroup, LedgerEntry
from app.schemas.writes import SplitIn
from app.services import entry_write_service as ews
from app.services import ledger_service
from app.services import split_service as ss
from app.services.edit_lock import EditLockedError
from app.services.errors import NotFoundError, ValidationError
from tests.helpers import race

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


def test_update_replaces_members_and_group_fields(client, db_session, seed):
    wallet, card = seed.account("錢包"), seed.account("卡")
    group_id = ss.create_split(
        db_session, SplitIn(**_split(_member(wallet, fee={"amount": "5"}), _member(wallet), name="舊"))
    )
    db_session.commit()
    old_ids = [m.id for m in _members(db_session, group_id)]
    wallet_id, card_id = wallet.id, card.id

    response = client.put(
        f"/splits/{group_id}",
        json=_split(_member(card, amount="70"), name="新", merchant="全家", entry_date="2026-09-05"),
    )

    assert response.status_code == 200
    members = _members(db_session, group_id)
    assert response.json() == {"group_id": group_id, "member_ids": [m.id for m in members]}
    assert not set(old_ids) & {m.id for m in members}
    assert [(m.account_id, m.amount, m.entry_date) for m in members] == [(card_id, Decimal("-70"), date(2026, 9, 5))]
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
    assert client.put(f"/splits/{hidden_id}", json=body).status_code == 200
    [member] = _members(db_session, hidden_id)
    assert (member.amount, member.source, member.moze_id) == (Decimal("-10"), "manual", None)


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

    response = client.put(f"/splits/{group_id}", json=_split(_member(wallet, amount="10")))

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["members"]]
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

    response = client.put(f"/splits/{group_id}", json=_split(_member(wallet, amount="10")))

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["members"]]
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

    response = client.put(f"/splits/{group_id}", json=_split(_member(a, amount="10")))

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["members"]]
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


def test_concurrent_split_updates_do_not_double_post(pg_engine, db_session, seed):
    # Plan review round 4 P1: with only the member rows locked, the second PUT re-queried after the first
    # committed, found none of the old members, and inserted its own beside the first's (−200 and −300 both
    # stayed, −500 in total). The group row lock makes the second PUT wait and then replace the first's members.
    wallet = seed.account("A", opening="1000")
    group_id = ss.create_split(db_session, SplitIn(**_split(_member(wallet, amount="100"))))
    db_session.commit()
    wallet_id = wallet.id
    assert _balance(db_session, wallet_id) == Decimal("900")

    def put(amount):
        body = _split({"account_id": wallet_id, "kind": "expense", "amount": amount})
        return lambda db: ss.update_split(db, group_id, SplitIn(**body))

    result = race(pg_engine, put("200"), put("300"))

    assert result == "committed"
    [member] = _members(db_session, group_id)
    assert member.amount == Decimal("-300")  # last writer wins
    assert _balance(db_session, wallet_id) == Decimal("700")  # moved by −300, not −500
    db_session.expire_all()
    assert db_session.scalars(select(LedgerEntry.amount).where(LedgerEntry.account_id == wallet_id)).all() == [
        Decimal("-300")
    ]


def test_delete_split_racing_update_leaves_one_outcome(pg_engine, db_session, seed):
    # The PUT holds the group lock; the DELETE waits on it, then sees and removes the PUT's members. Never both
    # sets, and never the PUT's members orphaned beside a deleted group.
    wallet = seed.account("A", opening="1000")
    group_id = ss.create_split(db_session, SplitIn(**_split(_member(wallet, amount="100"))))
    db_session.commit()
    wallet_id = wallet.id
    body = _split({"account_id": wallet_id, "kind": "expense", "amount": "300"})

    result = race(
        pg_engine,
        lambda db: ss.update_split(db, group_id, SplitIn(**body)),
        lambda db: ss.delete_split(db, group_id),
    )

    assert result == "committed" or isinstance(result, NotFoundError)
    db_session.expire_all()
    group = db_session.get(EntryGroup, group_id)
    remaining = db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == wallet_id)).all()
    if group is None:
        assert remaining == []  # deleted: nothing left, no orphaned members
        assert _balance(db_session, wallet_id) == Decimal("1000")
    else:
        assert [(m.group_id, m.amount) for m in remaining] == [(group_id, Decimal("-300"))]  # only the update's
        assert _balance(db_session, wallet_id) == Decimal("700")


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


def test_single_delete_racing_split_update_does_not_deadlock(pg_engine, db_session, seed):
    # Plan review round 5 P2: delete_entry locked the member first and the group only after deleting the last
    # member, the reverse of update_split (group → members). Now delete_entry takes lock_group first, so it waits
    # on the PUT's group lock; the PUT replaced every member, so the old member is gone (404), never a deadlock.
    wallet_id, group_id, [first_id, _] = _two_member_split(db_session, seed)
    body = _split({"account_id": wallet_id, "kind": "expense", "amount": "300"})

    result = race(
        pg_engine,
        lambda db: ss.update_split(db, group_id, SplitIn(**body)),
        lambda db: ews.delete_entry(db, first_id),
    )

    assert not isinstance(result, OperationalError)  # no deadlock / lock error
    assert result == "committed" or isinstance(result, NotFoundError)
    remaining = _assert_consistent(db_session, wallet_id, group_id)
    if isinstance(result, NotFoundError):  # update_split replaced the member: only the PUT's member is left
        assert [m.amount for m in remaining] == [Decimal("-300")]
    else:
        assert first_id not in [m.id for m in remaining]


def test_split_update_racing_single_delete_does_not_deadlock(pg_engine, db_session, seed):
    # The reverse order: the single DELETE holds the group lock (proved with NOWAIT from a third connection,
    # which fails only because delete_entry took lock_group before the member), the PUT waits on it, then
    # replaces whatever members the DELETE left.
    wallet_id, group_id, [first_id, _] = _two_member_split(db_session, seed)
    body = _split({"account_id": wallet_id, "kind": "expense", "amount": "300"})

    def delete_and_prove_group_lock(db):
        ews.delete_entry(db, first_id)
        with pg_engine.connect() as probe:
            with pytest.raises(OperationalError):  # LockNotAvailable: the group row is held by this delete
                probe.execute(select(EntryGroup.id).where(EntryGroup.id == group_id).with_for_update(nowait=True))
            probe.rollback()

    result = race(pg_engine, delete_and_prove_group_lock, lambda db: ss.update_split(db, group_id, SplitIn(**body)))

    assert not isinstance(result, OperationalError)  # no deadlock / lock error
    assert result == "committed" or isinstance(result, NotFoundError)
    remaining = _assert_consistent(db_session, wallet_id, group_id)
    if result == "committed":
        assert [m.amount for m in remaining] == [Decimal("-300")]
        assert _balance(db_session, wallet_id) == Decimal("700")


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
