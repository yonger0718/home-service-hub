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
