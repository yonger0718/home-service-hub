"""PUT /entries/{id}/split: convert a single entry into a split without losing its id (split rework §1.6)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import EntryGroup, LedgerEntry
from app.schemas.writes import EntryUpdateIn, SplitIn
from app.services import entry_write_service as ews
from app.services import ledger_service
from app.services import split_service as ss
from app.services.errors import ALREADY_GROUPED, RETRY, CodedConflictError, ConflictError, ValidationError
from tests.helpers import make_protected_member, race

DAY = date(2026, 9, 1)


def _member(account_id: int, **fields) -> dict:
    body = {"account_id": account_id, "kind": "expense", "amount": "30"}
    body.update(fields)
    return body


def _convert(anchor_id: int, account_id: int, *others, **fields) -> dict:
    anchor = {"id": anchor_id, "account_id": account_id, "kind": "expense", "amount": "70", "client_key": "a"}
    body = {"entry_date": DAY.isoformat(), "members": [anchor, *others]}
    body.update(fields)
    return body


def _groups(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(EntryGroup))


def _anchor(db_session, seed, **fields) -> tuple[int, int]:
    wallet = seed.account("錢包", opening="1000")
    anchor = seed.entry(wallet, "-100", name="晚餐", **fields)
    db_session.commit()
    return wallet.id, anchor.id


def test_convert_keeps_the_anchor_id(client, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)

    response = client.put(
        f"/entries/{anchor_id}/split",
        json=_convert(anchor_id, wallet_id, _member(wallet_id, client_key="b"), name="聚餐", merchant="鼎泰豐"),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    new_id = body["member_ids"][1]
    assert body == {"group_id": body["group_id"], "member_ids": [anchor_id, new_id],
                    "members": [{"id": anchor_id, "client_key": "a"}, {"id": new_id, "client_key": "b"}]}
    db_session.expire_all()
    group = db_session.get(EntryGroup, body["group_id"])
    anchor, other = db_session.get(LedgerEntry, anchor_id), db_session.get(LedgerEntry, new_id)
    assert (group.kind, group.name, group.merchant) == ("split", "聚餐", "鼎泰豐")
    assert (anchor.group_id, anchor.amount, other.group_id, other.amount) == (group.id, Decimal("-70"), group.id, Decimal("-30"))
    assert ledger_service.account_balance(db_session, wallet_id) == Decimal("900")


def test_invalid_second_member_leaves_the_anchor_alone(client, db_session, seed):
    # Review Focus 2: a member validation error → 422, the anchor unchanged, no group.
    wallet_id, anchor_id = _anchor(db_session, seed)

    response = client.put(f"/entries/{anchor_id}/split", json=_convert(anchor_id, wallet_id, _member(wallet_id, kind="receivable")))

    assert (response.status_code, [e["loc"] for e in response.json()["detail"]]) == (422, [["members.1.counterparty_id"]])
    db_session.expire_all()
    anchor = db_session.get(LedgerEntry, anchor_id)
    assert (anchor.amount, anchor.group_id, anchor.name) == (Decimal("-100"), None, "晚餐")
    assert _groups(db_session) == 0


def test_fx_failure_leaves_the_anchor_alone(db_session, seed, fake_http, today):
    # Review Focus 2: the FX provider is down for the second member → 422 members.1.fx_rate, nothing written.
    today(date(2026, 10, 6))
    wallet_id, anchor_id = _anchor(db_session, seed)
    http = fake_http({})
    body = SplitIn(**_convert(anchor_id, wallet_id, _member(wallet_id, amount=None, original_amount="1000", original_currency="JPY")))

    with pytest.raises(ValidationError) as error:
        ss.convert_to_split(db_session, anchor_id, body, http_get=http)
    db_session.rollback()

    assert error.value.field == "members.1.fx_rate"
    assert http.calls  # the rate was attempted, and failed
    anchor = db_session.get(LedgerEntry, anchor_id)
    assert (anchor.amount, anchor.group_id) == (Decimal("-100"), None)
    assert _groups(db_session) == 0


@pytest.mark.parametrize(
    "case, code",
    [
        ("transfer", "entry_locked"), ("fee", "entry_locked"), ("settlement", "entry_locked"),
        ("orphan_settlement", "entry_locked"), ("refunded_original", "entry_locked"), ("orphan_refund", "entry_locked"),
        ("scheduled_loan", "entry_locked"),
    ],
)
def test_protected_anchor_is_refused(client, db_session, seed, case, code):
    wallet = seed.account("錢包")
    anchor = make_protected_member(seed, case, wallet, None)
    db_session.commit()
    wallet_id, anchor_id = wallet.id, anchor.id

    response = client.put(f"/entries/{anchor_id}/split", json=_convert(anchor_id, wallet_id, _member(wallet_id)))

    assert (response.status_code, response.json()["message"].split(":")[0]) == (409, code)
    assert _groups(db_session) == 0


def test_grouped_scheduled_and_imported_anchors_are_refused(client, db_session, seed):
    wallet = seed.account("錢包")
    group = seed.group()
    grouped = seed.entry(wallet, "-100", group_id=group.id)
    scheduled = seed.entry(wallet, "-390", source="schedule")
    imported = seed.entry(wallet, "-50", source="moze_backup", moze_id="rec-1")
    db_session.commit()
    wallet_id = wallet.id

    answers = [
        client.put(f"/entries/{entry_id}/split", json=_convert(entry_id, wallet_id, _member(wallet_id)))
        for entry_id in (grouped.id, scheduled.id, imported.id)
    ]

    assert [(r.status_code, r.json()["message"].split(":")[0]) for r in answers] == [
        (409, "already_grouped"), (409, "kind_not_splittable"), (409, "locked_until_cutover"),
    ]
    assert _groups(db_session) == 1  # only the pre-existing group


def test_convert_cardinality(client, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)
    anchor_only = _convert(anchor_id, wallet_id)
    no_anchor = {"entry_date": DAY.isoformat(), "members": [_member(wallet_id), _member(wallet_id)]}
    stranger = _convert(anchor_id, wallet_id, _member(wallet_id, id=anchor_id + 1000))
    keep = _convert(anchor_id, wallet_id, {"id": anchor_id + 1000, "keep": True})

    answers = [client.put(f"/entries/{anchor_id}/split", json=body) for body in (anchor_only, no_anchor, stranger, keep)]
    missing = client.put("/entries/999999/split", json=_convert(999999, wallet_id, _member(wallet_id)))

    assert [(r.status_code, r.json()["detail"][0]["loc"]) for r in answers] == [
        (422, ["members"]), (422, ["members"]), (422, ["members.1.id"]), (422, ["members.1.keep"]),
    ]
    assert missing.status_code == 404
    assert _groups(db_session) == 0


def test_a_retry_after_success_is_already_grouped(client, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)
    body = _convert(anchor_id, wallet_id, _member(wallet_id))

    first = client.put(f"/entries/{anchor_id}/split", json=body)
    again = client.put(f"/entries/{anchor_id}/split", json=body)

    assert first.status_code == 200
    assert (again.status_code, again.json()["message"].split(":")[0]) == (409, "already_grouped")
    assert _groups(db_session) == 1
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 2


def test_concurrent_converts_one_wins(pg_engine, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)
    body = SplitIn(**_convert(anchor_id, wallet_id, _member(wallet_id)))

    result = race(pg_engine, lambda db: ss.convert_to_split(db, anchor_id, body), lambda db: ss.convert_to_split(db, anchor_id, body))

    assert isinstance(result, CodedConflictError) and result.code == ALREADY_GROUPED
    assert _groups(db_session) == 1
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 2


def test_convert_after_a_concurrent_delete_of_the_anchor_retries(pg_engine, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)
    body = SplitIn(**_convert(anchor_id, wallet_id, _member(wallet_id)))

    result = race(pg_engine, lambda db: ews.delete_entry(db, anchor_id), lambda db: ss.convert_to_split(db, anchor_id, body))

    assert isinstance(result, CodedConflictError) and result.code == RETRY
    assert _groups(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 0


def test_delete_after_a_concurrent_convert_of_the_anchor_retries(pg_engine, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)
    body = SplitIn(**_convert(anchor_id, wallet_id, _member(wallet_id)))

    result = race(pg_engine, lambda db: ss.convert_to_split(db, anchor_id, body), lambda db: ews.delete_entry(db, anchor_id))

    assert isinstance(result, ConflictError)  # the anchor joined a group under the delete: re-read and retry
    assert _groups(db_session) == 1
    assert db_session.get(LedgerEntry, anchor_id).group_id is not None


@pytest.mark.parametrize("convert_first", [True, False])
def test_convert_and_an_anchor_update_serialise(pg_engine, db_session, seed, convert_first):
    wallet_id, anchor_id = _anchor(db_session, seed)
    body = SplitIn(**_convert(anchor_id, wallet_id, _member(wallet_id)))
    update = EntryUpdateIn(account_id=wallet_id, kind="expense", amount="80", entry_date=DAY)
    convert = lambda db: ss.convert_to_split(db, anchor_id, body)  # noqa: E731
    put = lambda db: ews.update_entry(db, anchor_id, update)  # noqa: E731

    result = race(pg_engine, convert, put) if convert_first else race(pg_engine, put, convert)

    assert result == "committed"
    db_session.expire_all()
    anchor = db_session.get(LedgerEntry, anchor_id)
    assert anchor.group_id is not None  # the PUT /entries never detaches a member
    assert anchor.amount == (Decimal("-80") if convert_first else Decimal("-70"))  # the later writer wins
    assert _groups(db_session) == 1
