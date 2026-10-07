"""Dissolving a split (split rework §1.6 dissolve, §1.7 DELETE auto-dissolve)."""

import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import EntryGroup, EntryRewardRule, LedgerEntry, ScheduleInstance
from app.schemas.writes import EntryUpdateIn, SplitIn
from app.services import entry_write_service as ews
from app.services import schedule_posting as posting
from app.services import schedule_service
from app.services import split_service as ss
from app.services.errors import RETRY, CodedConflictError, NotFoundError
from tests.helpers import race

DAY = date(2026, 9, 1)


def _split(*members, **fields) -> dict:
    body = {"entry_date": DAY.isoformat(), "members": list(members)}
    body.update(fields)
    return body


def _keep(entry_id: int, **fields) -> dict:
    return {"id": entry_id, "keep": True, **fields}


def _row(db, entry_id: int) -> LedgerEntry | None:
    db.expire_all()
    return db.get(LedgerEntry, entry_id)


def _group_of_two(db_session, seed, **survivor_fields) -> tuple[int, int, int, int]:
    """A split 聚餐 / 鼎泰豐 / 生日 of two expenses on one wallet; returns wallet, group, doomed and survivor ids."""
    wallet = seed.account("錢包", opening="1000")
    group = seed.group(name="聚餐", merchant="鼎泰豐", description="生日")
    doomed = seed.entry(wallet, "-100", group_id=group.id)
    survivor = seed.entry(wallet, "-50", group_id=group.id, **survivor_fields)
    db_session.commit()
    return wallet.id, group.id, doomed.id, survivor.id


def test_dissolve_with_a_full_member_fills_blank_fields_from_the_payload(client, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed, merchant="自己的店", description="  ")
    survivor = {"id": survivor_id, "account_id": wallet_id, "kind": "expense", "amount": "50", "merchant": "自己的店",
                "description": "  "}

    response = client.put(f"/splits/{group_id}", json=_split(survivor, name="新名", merchant="新店", description="新備註"))

    assert response.status_code == 200, response.text
    assert response.json() == {"group_id": None, "member_ids": [survivor_id], "members": [{"id": survivor_id, "client_key": None}]}
    row = _row(db_session, survivor_id)
    # field by field, from the payload's parent values (not the stored 聚餐/鼎泰豐/生日); whitespace counts as empty
    assert (row.name, row.merchant, row.description, row.group_id) == ("新名", "自己的店", "新備註", None)
    assert db_session.get(EntryGroup, group_id) is None
    assert db_session.get(LedgerEntry, doomed_id) is None


def test_dissolve_with_a_keep_member(client, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)

    response = client.put(f"/splits/{group_id}", json=_split(_keep(survivor_id, name="自取", client_key="s"), name="新名", merchant="新店"))

    assert response.status_code == 200, response.text
    assert response.json()["members"] == [{"id": survivor_id, "client_key": "s"}]
    row = _row(db_session, survivor_id)
    assert (row.name, row.merchant, row.description, row.group_id, row.amount) == ("自取", "新店", None, None, Decimal("-50"))


def test_dissolve_onto_a_protected_survivor_keeps_its_dates(client, db_session, seed):
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    group = seed.group(name="代墊")
    seed.entry(wallet, "-100", group_id=group.id)
    collection = seed.entry(wallet, "200", kind="receivable", counterparty_id=alan.id, is_settlement=True,
                            group_id=group.id, entry_time=time(8, 5))
    db_session.commit()
    group_id, wallet_id, alan_id, collection_id = group.id, wallet.id, alan.id, collection.id
    as_full = {"id": collection_id, "account_id": wallet_id, "kind": "receivable", "amount": "200", "counterparty_id": alan_id}

    refused = client.put(f"/splits/{group_id}", json=_split(as_full, entry_date="2026-09-09"))
    kept = client.put(f"/splits/{group_id}", json=_split(_keep(collection_id), name="代墊", entry_date="2026-09-09"))

    assert (refused.status_code, refused.json()["message"].split(":")[0]) == (409, "member_locked")
    assert (kept.status_code, kept.json()["group_id"]) == (200, None)
    row = _row(db_session, collection_id)
    assert (row.amount, row.is_settlement, row.entry_date, row.entry_time, row.name, row.group_id) == (
        Decimal("200"), True, DAY, time(8, 5), "代墊", None,
    )


def test_dissolve_refusals(client, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)
    other_wallet = seed.account("別的")
    other_group = seed.group()
    foreign = seed.entry(other_wallet, "-1", group_id=other_group.id)
    db_session.commit()

    no_id = client.put(f"/splits/{group_id}", json=_split({"account_id": wallet_id, "kind": "expense", "amount": "5"}))
    foreign_id = client.put(f"/splits/{group_id}", json=_split(_keep(foreign.id)))

    assert (no_id.status_code, no_id.json()["detail"][0]["loc"]) == (422, ["members.0.id"])
    assert (foreign_id.status_code, foreign_id.json()["message"].split(":")[0]) == (404, "member_not_found")
    assert db_session.get(EntryGroup, group_id) is not None


def test_only_split_groups_dissolve(client, db_session, seed):
    wallet = seed.account()
    group = seed.group(kind="installment", name="分期")
    first = seed.entry(wallet, "-100", group_id=group.id)
    seed.entry(wallet, "-100", group_id=group.id)
    db_session.commit()

    response = client.put(f"/splits/{group.id}", json=_split(_keep(first.id)))

    assert response.status_code == 404
    assert _row(db_session, first.id).group_id == group.id


def test_delete_down_to_one_member_dissolves_the_split(client, db_session, seed):
    # Survivor fields and links intact; only its blank fields take the group's values.
    wallet = seed.account("錢包", opening="1000")
    alan = seed.counterparty("Alan")
    rule = seed.rule(wallet)
    group = seed.group(name="聚餐", merchant="鼎泰豐", description="生日")
    doomed = seed.entry(wallet, "-100", group_id=group.id)
    survivor = seed.entry(wallet, "-300", kind="receivable", counterparty_id=alan.id, group_id=group.id,
                          merchant="自己的店", description="  ")
    fee = seed.entry(wallet, "-15", kind="fee", parent_entry_id=survivor.id, name="手續費")
    collection = seed.entry(wallet, "100", kind="receivable", counterparty_id=alan.id, is_settlement=True,
                            settles_entry_id=survivor.id)
    db_session.add(EntryRewardRule(entry_id=survivor.id, rule_id=rule.id))
    db_session.commit()
    ids = {"group": group.id, "doomed": doomed.id, "survivor": survivor.id, "fee": fee.id, "collection": collection.id}

    assert client.delete(f"/entries/{ids['doomed']}").status_code == 204

    row = _row(db_session, ids["survivor"])
    assert (row.name, row.merchant, row.description, row.group_id, row.amount) == (
        "聚餐", "自己的店", "生日", None, Decimal("-300"),
    )
    assert db_session.get(EntryGroup, ids["group"]) is None
    assert db_session.get(LedgerEntry, ids["fee"]).parent_entry_id == ids["survivor"]
    assert db_session.get(LedgerEntry, ids["collection"]).settles_entry_id == ids["survivor"]
    assert db_session.scalars(select(EntryRewardRule.rule_id).where(EntryRewardRule.entry_id == ids["survivor"])).all() == [rule.id]


def test_installment_group_is_never_dissolved(client, db_session, seed):
    wallet = seed.account()
    group = seed.group(kind="installment", name="分期")
    first = seed.entry(wallet, "-100", group_id=group.id)
    second = seed.entry(wallet, "-100", group_id=group.id)
    db_session.commit()
    group_id, first_id, second_id = group.id, first.id, second.id

    assert client.delete(f"/entries/{first_id}").status_code == 204
    assert _row(db_session, second_id).group_id == group_id  # one member left: kept as is
    assert client.delete(f"/entries/{second_id}").status_code == 204
    db_session.expire_all()
    assert db_session.get(EntryGroup, group_id) is None  # count 0: today's cleanup


def test_scheduled_split_period_keeps_partial_and_reopen_semantics(client, db_session, seed, today):
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    bundle = seed.definition(
        [seed.line("expense", card, "390"), seed.line("expense", card, "149"), seed.line("expense", card, "60")],
        name="串流組合",
    )
    instance = seed.instance(bundle, 1, date(2026, 10, 22))
    first_id, second_id, third_id = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    instance_id = instance.id
    group_id = db_session.get(LedgerEntry, first_id).group_id

    assert client.delete(f"/entries/{first_id}").status_code == 204
    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance_id)
    assert (row.status, row.is_partial, sorted(row.posted_entry_ids)) == ("posted", True, sorted([second_id, third_id]))
    assert db_session.get(EntryGroup, group_id) is not None  # two members left

    assert client.delete(f"/entries/{second_id}").status_code == 204
    db_session.expire_all()
    assert (db_session.get(ScheduleInstance, instance_id).posted_entry_ids, db_session.get(EntryGroup, group_id)) == ([third_id], None)
    assert db_session.get(LedgerEntry, third_id).group_id is None

    assert client.delete(f"/entries/{third_id}").status_code == 204
    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance_id)
    assert (row.status, row.posted_entry_ids, row.reopened_at is not None) == ("pending", [], True)


def test_transfer_legs_in_two_splits_dissolve_both(client, db_session, seed):
    # Both legs' groups are expanded up front: one lock set, both survivors dissolved, no dangling group.
    a, b = seed.account("A"), seed.account("B")
    first_group, second_group = seed.group(name="一"), seed.group(name="二")
    pair = uuid.uuid4()
    out_leg = seed.entry(a, "-100", kind="transfer_out", transfer_group_id=pair, group_id=first_group.id)
    in_leg = seed.entry(b, "100", kind="transfer_in", transfer_group_id=pair, group_id=second_group.id)
    x = seed.entry(a, "-10", group_id=first_group.id)
    y = seed.entry(b, "-20", group_id=second_group.id)
    db_session.commit()
    ids = (out_leg.id, in_leg.id, x.id, y.id, first_group.id, second_group.id)

    assert client.delete(f"/entries/{ids[0]}").status_code == 204

    db_session.expire_all()
    assert db_session.get(LedgerEntry, ids[0]) is None and db_session.get(LedgerEntry, ids[1]) is None
    assert db_session.get(EntryGroup, ids[4]) is None and db_session.get(EntryGroup, ids[5]) is None
    assert [(db_session.get(LedgerEntry, i).group_id, db_session.get(LedgerEntry, i).name) for i in ids[2:4]] == [
        (None, "一"), (None, "二"),
    ]


@pytest.mark.parametrize("delete_first", [True, False])
def test_delete_dissolve_races_a_survivor_put(pg_engine, db_session, seed, delete_first):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)
    update = EntryUpdateIn(account_id=wallet_id, kind="expense", amount="70", entry_date=DAY)
    delete = lambda db: ews.delete_entry(db, doomed_id)  # noqa: E731
    put = lambda db: ews.update_entry(db, survivor_id, update)  # noqa: E731

    result = race(pg_engine, delete, put) if delete_first else race(pg_engine, put, delete)

    assert result == "committed"
    row = _row(db_session, survivor_id)
    assert (row.amount, row.group_id) == (Decimal("-70"), None)
    assert db_session.get(EntryGroup, group_id) is None and db_session.get(LedgerEntry, doomed_id) is None


def test_two_deletes_of_a_two_member_split_queue(pg_engine, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)

    result = race(pg_engine, lambda db: ews.delete_entry(db, doomed_id), lambda db: ews.delete_entry(db, survivor_id))

    assert isinstance(result, CodedConflictError) and result.code == RETRY  # the group dissolved under the second
    assert _row(db_session, survivor_id).group_id is None
    assert db_session.get(EntryGroup, group_id) is None
    ews.delete_entry(db_session, survivor_id)  # the client's retry
    db_session.commit()
    assert db_session.scalars(select(LedgerEntry)).all() == []


def test_delete_dissolve_then_group_put_retries(pg_engine, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)
    body = SplitIn(**_split(
        {"id": survivor_id, "account_id": wallet_id, "kind": "expense", "amount": "70"},
        {"account_id": wallet_id, "kind": "expense", "amount": "5"},
    ))

    result = race(pg_engine, lambda db: ews.delete_entry(db, doomed_id), lambda db: ss.update_split(db, group_id, body))

    assert isinstance(result, CodedConflictError) and result.code == RETRY
    row = _row(db_session, survivor_id)
    assert (row.amount, row.group_id) == (Decimal("-50"), None)


def test_group_put_then_delete_finds_the_member_gone(pg_engine, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)
    body = SplitIn(**_split(
        {"id": survivor_id, "account_id": wallet_id, "kind": "expense", "amount": "70"},
        {"account_id": wallet_id, "kind": "expense", "amount": "5"},
    ))

    result = race(pg_engine, lambda db: ss.update_split(db, group_id, body), lambda db: ews.delete_entry(db, doomed_id))

    assert isinstance(result, NotFoundError)  # the PUT dropped it first
    db_session.expire_all()
    members = db_session.scalars(select(LedgerEntry).where(LedgerEntry.group_id == group_id).order_by(LedgerEntry.id)).all()
    assert [(m.id == survivor_id, m.amount) for m in members] == [(True, Decimal("-70")), (False, Decimal("-5"))]


@pytest.fixture()
def scheduled_pair(db_session, seed, today):
    """A posted two-line split period (串流組合 #1) on 2026-10-22."""
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    bundle = seed.definition([seed.line("expense", card, "390"), seed.line("expense", card, "149")], name="串流組合")
    instance = seed.instance(bundle, 1, date(2026, 10, 22))
    first_id, second_id = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    return instance.id, first_id, second_id


@pytest.mark.parametrize("delete_first", [True, False])
def test_delete_dissolve_races_a_schedule_repost(pg_engine, db_session, scheduled_pair, delete_first):
    instance_id, first_id, second_id = scheduled_pair
    delete = lambda db: ews.delete_entry(db, first_id)  # noqa: E731
    repost = lambda db: schedule_service.repost_instance(db, instance_id, ["390", "149"])  # noqa: E731

    result = race(pg_engine, delete, repost) if delete_first else race(pg_engine, repost, delete)

    assert result == "committed" if delete_first else isinstance(result, NotFoundError)
    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance_id)
    assert (row.status, row.is_partial, len(row.posted_entry_ids)) == ("posted", False, 2)
    posted = [db_session.get(LedgerEntry, entry_id) for entry_id in row.posted_entry_ids]
    assert len({entry.group_id for entry in posted}) == 1 and posted[0].group_id is not None
    assert db_session.get(LedgerEntry, second_id) is None  # the repost replaced the survivor too
