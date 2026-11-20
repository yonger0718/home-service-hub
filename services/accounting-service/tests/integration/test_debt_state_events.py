"""#401: every close / reopen of a debt writes a `debt_state_event` that no entry edit or delete removes. A close with
a remainder by a caller without the owner's scope waits on `GET /debt-events/to-review` until the owner posts its
review. Fixtures are fictional."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app import auth
from app.models import Account, Counterparty, DebtStateEvent, LedgerEntry
from app.services import ledger_service

BOT = {"Authorization": "Bearer bot-token"}  # write only: its closes wait for the owner
OWNER = {"Authorization": "Bearer owner-token"}  # admin
WEB = {"Authorization": "Bearer web-token"}  # legacy
READER = {"Authorization": "Bearer rd-token"}
WORKER = {"Authorization": "Bearer wk-token"}  # ingest only: no ledger read
DAY = date(2026, 9, 1)


@pytest.fixture
def scoped(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, "bot:bot-token,owner:owner-token,web:web-token,rd:rd-token,wk:wk-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "bot=read,write; owner=read,write,admin; web=legacy; rd=read; wk=ingest")


def _receivable(seed, db, *, collected=None, closed=False, name="晚餐代墊", who="Alan", amount="-420"):
    """A receivable of 420 on a card, optionally with one collection → (card, receivable id)."""
    def named(model, name, make):
        return db.scalar(select(model).where(model.name == name)) or make(name)

    card, wallet = named(Account, "卡", seed.account), named(Account, "錢包", seed.account)
    person = named(Counterparty, who, seed.counterparty)
    receivable = seed.entry(
        card, amount, kind="receivable", counterparty_id=person.id, name=name, description="備註原文", is_closed=closed)
    if collected is not None:
        seed.entry(
            wallet, collected, kind="receivable", counterparty_id=person.id, settles_entry_id=receivable.id,
            is_settlement=True,
        )
    db.commit()
    return card, receivable.id


def _events(db) -> list[DebtStateEvent]:
    db.expire_all()
    return list(db.scalars(select(DebtStateEvent).order_by(DebtStateEvent.id)))


def _detail_events(client, entry_id, headers=None) -> list[dict]:
    return client.get(f"/entries/{entry_id}", headers=headers).json()["debt_events"]


def _to_review(client, headers=None, **params) -> dict:
    response = client.get("/debt-events/to-review", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- writing ---------------------------------------------------------------------------------------------------------


def test_a_close_writes_one_event_and_leaves_the_description_alone(client, db_session, seed, scoped):
    card, receivable_id = _receivable(seed, db_session, collected="200")

    response = client.post(f"/entries/{receivable_id}/close", headers=BOT)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["description"] == "備註原文"
    [event] = body["debt_events"]
    assert {key: event[key] for key in ("action", "actor", "open_amount", "currency", "event_date", "needs_review")} == {
        "action": "close", "actor": "bot", "open_amount": "220.0000", "currency": card.currency,
        "event_date": ledger_service._today().isoformat(), "needs_review": True,
    }
    [row] = _events(db_session)
    assert (row.id, row.entry_id, row.action, row.actor, row.open_amount, row.currency) == (
        event["id"], receivable_id, "close", "bot", Decimal("220"), card.currency)
    assert (row.entry_kind, row.entry_name, row.counterparty, row.entry_amount, row.entry_date, row.reviewed_at) == (
        "receivable", "晚餐代墊", "Alan", Decimal("-420"), DAY, None)


def test_a_reopen_writes_the_remainder_it_brings_back(client, db_session, seed, scoped):
    _, receivable_id = _receivable(seed, db_session, collected="200", closed=True)

    response = client.post(f"/entries/{receivable_id}/reopen", headers=BOT)

    assert response.status_code == 200, response.text
    [event] = response.json()["debt_events"]
    assert (event["action"], event["actor"], event["open_amount"], event["needs_review"]) == (
        "reopen", "bot", "220.0000", False)


@pytest.mark.parametrize("headers", [BOT, OWNER], ids=["write-only", "admin"])
def test_a_close_with_nothing_left_is_refused_and_writes_nothing(client, db_session, seed, scoped, headers):
    """#403: a debt its settlements cleared needs no flag; closed at zero it could hide a remainder that appears later."""
    _, receivable_id = _receivable(seed, db_session, collected="420")

    response = client.post(f"/entries/{receivable_id}/close", headers=headers)

    assert response.status_code == 422, response.text
    assert {str(error["loc"][-1]) for error in response.json()["detail"]} == {"open_amount"}
    assert client.get(f"/entries/{receivable_id}", headers=headers).json()["is_closed"] is False
    assert _events(db_session) == []


def test_a_caller_cannot_take_its_waiting_close_off_the_list_through_a_close_at_zero(client, db_session, seed, scoped):
    """#403: reopen → settle in full → close → delete the settlement. The close is refused, so the debt stays open:
    cleared while the settlement stands, and back in the open debts once it is deleted."""
    card, receivable_id = _receivable(seed, db_session, collected="200")
    client.post(f"/entries/{receivable_id}/close", headers=BOT)
    assert _to_review(client, READER)["total"] == 1

    assert client.post(f"/entries/{receivable_id}/reopen", headers=BOT).status_code == 200
    settled = client.post(
        f"/entries/{receivable_id}/settle", headers=BOT,
        json={"account_id": card.id, "amount": "220", "entry_date": DAY.isoformat()})
    assert settled.status_code in (200, 201), settled.text
    assert client.post(f"/entries/{receivable_id}/close", headers=BOT).status_code == 422
    settlement_id = max(row["id"] for row in client.get(f"/entries/{receivable_id}", headers=BOT).json()["settled_by"])
    assert client.delete(f"/entries/{settlement_id}", headers=BOT).status_code == 200

    open_ids = [row["id"] for row in client.get("/entries", headers=READER, params={"open": "true"}).json()["items"]]
    assert receivable_id in open_ids
    assert [event.action for event in _events(db_session)] == ["close", "reopen"]


def test_repeated_calls_and_refusals_write_nothing(client, db_session, seed, scoped):
    card, receivable_id = _receivable(seed, db_session)
    expense = seed.entry(card, "-80")
    db_session.commit()

    assert client.post(f"/entries/{receivable_id}/reopen", headers=BOT).status_code == 200  # already open
    assert client.post(f"/entries/{expense.id}/close", headers=BOT).status_code == 422
    assert client.post("/entries/999999/close", headers=BOT).status_code == 404
    assert client.post(f"/entries/{receivable_id}/close", headers=READER).status_code == 403
    assert _events(db_session) == []

    assert client.post(f"/entries/{receivable_id}/close", headers=BOT).status_code == 200
    assert client.post(f"/entries/{receivable_id}/close", headers=BOT).status_code == 200
    assert [event.action for event in _events(db_session)] == ["close"]


def test_without_auth_the_actor_is_null_and_the_close_needs_no_review(client, db_session, seed):
    _, receivable_id = _receivable(seed, db_session)

    response = client.post(f"/entries/{receivable_id}/close")

    [event] = response.json()["debt_events"]
    assert (event["actor"], event["needs_review"]) == (None, False)
    [row] = _events(db_session)
    assert row.reviewed_at is not None and row.reviewed_at == row.created_at
    assert _to_review(client) == {"items": [], "total": 0}


def test_a_bare_token_without_scopes_closes_with_a_null_actor_and_no_review(client, db_session, seed, monkeypatch):
    # tokens configured, scopes not: every token is the owner's, and a token without a label has no name to record
    monkeypatch.setenv(auth.TOKENS_ENV, "plain-token")
    _, receivable_id = _receivable(seed, db_session)
    plain = {"Authorization": "Bearer plain-token"}

    response = client.post(f"/entries/{receivable_id}/close", headers=plain)

    assert response.status_code == 200, response.text
    [event] = response.json()["debt_events"]
    assert (event["actor"], event["needs_review"]) == (None, False)
    assert _to_review(client, plain) == {"items": [], "total": 0}


def test_a_bare_token_holds_no_scope_once_scopes_are_configured(client, db_session, seed, monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, "plain-token,owner:owner-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "owner=read,write,admin")
    _, receivable_id = _receivable(seed, db_session)

    response = client.post(f"/entries/{receivable_id}/close", headers={"Authorization": "Bearer plain-token"})

    assert response.status_code == 403, response.text
    assert _events(db_session) == []


def test_the_detail_lists_the_events_oldest_first_and_only_the_last_waits(client, db_session, seed, scoped):
    _, receivable_id = _receivable(seed, db_session)

    for action in ("close", "reopen", "close"):
        assert client.post(f"/entries/{receivable_id}/{action}", headers=BOT).status_code == 200

    events = _detail_events(client, receivable_id, BOT)
    assert [(event["action"], event["needs_review"]) for event in events] == [
        ("close", False), ("reopen", False), ("close", True)]
    assert [event["id"] for event in events] == sorted(event["id"] for event in events)


def test_a_row_without_events_answers_an_empty_list(client, db_session, seed):
    card = seed.account("卡")
    expense = seed.entry(card, "-80")
    db_session.commit()

    assert _detail_events(client, expense.id) == []


def test_the_events_stay_in_the_detail_when_the_row_stops_being_a_debt(client, db_session, seed, scoped):
    """The list still links to the entry: its detail must show why."""
    _, receivable_id = _receivable(seed, db_session, collected="200")
    client.post(f"/entries/{receivable_id}/close", headers=BOT)
    db_session.execute(update(LedgerEntry).where(LedgerEntry.id == receivable_id).values(kind="expense"))
    db_session.commit()

    assert [(e["action"], e["needs_review"]) for e in _detail_events(client, receivable_id, READER)] == [("close", True)]
    assert [item["entry_id"] for item in _to_review(client, READER)["items"]] == [receivable_id]


def test_the_events_stay_when_the_entry_is_deleted(client, db_session, seed, scoped):
    _, receivable_id = _receivable(seed, db_session)
    client.post(f"/entries/{receivable_id}/close", headers=BOT)

    assert client.delete(f"/entries/{receivable_id}", headers=BOT).status_code == 200

    [row] = _events(db_session)
    assert (row.entry_id, row.action, row.entry_name) == (receivable_id, "close", "晚餐代墊")


# --- the list that waits for the owner --------------------------------------------------------------------------------


def test_a_close_with_a_remainder_by_a_write_only_caller_waits_for_review(client, db_session, seed, scoped):
    card, receivable_id = _receivable(seed, db_session, collected="200")
    event_id = client.post(f"/entries/{receivable_id}/close", headers=BOT).json()["debt_events"][0]["id"]

    assert _to_review(client, READER) == {
        "total": 1,
        "items": [{
            "event_id": event_id, "entry_id": receivable_id, "deleted": False, "kind": "receivable",
            "name": "晚餐代墊", "counterparty": "Alan", "entry_date": DAY.isoformat(), "amount": "-420.0000",
            "currency": card.currency, "open_amount": "220.0000", "current_open_amount": "220.0000", "actor": "bot",
            "event_date": ledger_service._today().isoformat(),
        }],
    }


def test_the_list_answers_the_remainder_at_the_close_and_the_one_now(client, db_session, seed, scoped):
    """#403: a settlement deleted after the close moves the remainder; the owner confirms what is written off now."""
    _, moved_id = _receivable(seed, db_session, collected="200", who="Bea")
    _, still_id = _receivable(seed, db_session, collected="200", who="Cal")
    _, gone_id = _receivable(seed, db_session, who="Dee")
    for entry_id in (moved_id, still_id, gone_id):
        assert client.post(f"/entries/{entry_id}/close", headers=BOT).status_code == 200
    [settlement] = client.get(f"/entries/{moved_id}", headers=BOT).json()["settled_by"]
    assert client.delete(f"/entries/{settlement['id']}", headers=BOT).status_code == 200
    assert client.delete(f"/entries/{gone_id}", headers=BOT).status_code == 200

    amounts = {item["entry_id"]: (item["open_amount"], item["current_open_amount"]) for item in _to_review(client, READER)["items"]}

    assert amounts == {
        moved_id: ("220.0000", "420.0000"), still_id: ("220.0000", "220.0000"), gone_id: ("420.0000", None),
    }


def _amount_edit(client, card, entry_id, amount):
    stored = client.get(f"/entries/{entry_id}", headers=BOT).json()
    body = {"account_id": card.id, "kind": "receivable", "amount": amount, "entry_date": stored["entry_date"],
            "counterparty_id": stored["counterparty_id"], "name": stored["name"]}
    return client.put(f"/entries/{entry_id}", headers=BOT, json=body)


def test_an_amount_edit_cannot_clear_a_waiting_close(client, db_session, seed, scoped):
    """#403: the amount of a debt is locked while a settlement points at it, so no edit brings the remainder of a
    waiting close to nothing; without settlements the edit goes through and the row shows both remainders."""
    card, settled_id = _receivable(seed, db_session, collected="200", who="Bea")
    _, plain_id = _receivable(seed, db_session, who="Cal")
    for entry_id in (settled_id, plain_id):
        assert client.post(f"/entries/{entry_id}/close", headers=BOT).status_code == 200

    locked = _amount_edit(client, card, settled_id, "200")
    zeroed = _amount_edit(client, card, plain_id, "0")
    lowered = _amount_edit(client, card, plain_id, "100")

    assert locked.status_code == 422 and {str(e["loc"][-1]) for e in locked.json()["detail"]} == {"amount"}
    assert zeroed.status_code == 422 and {str(e["loc"][-1]) for e in zeroed.json()["detail"]} == {"amount"}
    assert lowered.status_code == 200, lowered.text
    amounts = {i["entry_id"]: (i["open_amount"], i["current_open_amount"]) for i in _to_review(client, OWNER)["items"]}
    assert amounts == {settled_id: ("220.0000", "220.0000"), plain_id: ("420.0000", "100.0000")}


def test_a_live_debt_answers_its_current_name_not_the_snapshot(client, db_session, seed, scoped):
    _, receivable_id = _receivable(seed, db_session)
    client.post(f"/entries/{receivable_id}/close", headers=BOT)
    db_session.execute(update(LedgerEntry).where(LedgerEntry.id == receivable_id).values(name="改名後"))
    db_session.commit()

    assert [(item["name"], item["deleted"]) for item in _to_review(client, READER)["items"]] == [("改名後", False)]
    assert _events(db_session)[0].entry_name == "晚餐代墊"


def test_the_list_needs_the_read_scope(client, db_session, seed, scoped):
    _, receivable_id = _receivable(seed, db_session)
    client.post(f"/entries/{receivable_id}/close", headers=BOT)

    response = client.get("/debt-events/to-review", headers=WORKER)

    assert (response.status_code, response.json()["message"]) == (403, "scope")


@pytest.mark.parametrize("headers", [OWNER, WEB], ids=["admin", "legacy"])
def test_a_close_by_the_owner_never_waits(client, db_session, seed, scoped, headers):
    _, receivable_id = _receivable(seed, db_session)

    response = client.post(f"/entries/{receivable_id}/close", headers=headers)

    assert response.json()["debt_events"][0]["needs_review"] is False
    assert _to_review(client, OWNER) == {"items": [], "total": 0}


def test_a_close_by_the_owner_after_a_reopen_replaces_the_waiting_close(client, db_session, seed, scoped):
    _, receivable_id = _receivable(seed, db_session)

    client.post(f"/entries/{receivable_id}/close", headers=BOT)
    assert _to_review(client, OWNER)["total"] == 1
    client.post(f"/entries/{receivable_id}/reopen", headers=OWNER)
    client.post(f"/entries/{receivable_id}/close", headers=OWNER)

    assert _to_review(client, OWNER) == {"items": [], "total": 0}
    events = _detail_events(client, receivable_id, OWNER)
    assert [(e["action"], e["actor"], e["needs_review"]) for e in events] == [
        ("close", "bot", False), ("reopen", "owner", False), ("close", "owner", False)]


def test_a_reopened_debt_does_not_wait(client, db_session, seed, scoped):
    _, reopened_id = _receivable(seed, db_session, who="Cal")

    client.post(f"/entries/{reopened_id}/close", headers=BOT)
    client.post(f"/entries/{reopened_id}/reopen", headers=BOT)

    assert _to_review(client, OWNER) == {"items": [], "total": 0}


def test_a_deleted_debt_is_listed_with_its_snapshot(client, db_session, seed, scoped):
    card, receivable_id = _receivable(seed, db_session)
    client.post(f"/entries/{receivable_id}/close", headers=BOT)
    assert client.delete(f"/entries/{receivable_id}", headers=BOT).status_code == 200

    [item] = _to_review(client, OWNER)["items"]

    assert {key: item[key] for key in ("entry_id", "deleted", "kind", "name", "counterparty", "entry_date", "amount",
                                       "currency", "open_amount", "actor")} == {
        "entry_id": receivable_id, "deleted": True, "kind": "receivable", "name": "晚餐代墊", "counterparty": "Alan",
        "entry_date": DAY.isoformat(), "amount": "-420.0000", "currency": card.currency, "open_amount": "420.0000",
        "actor": "bot",
    }


def test_a_debt_reopened_before_its_delete_is_not_listed(client, db_session, seed, scoped):
    _, receivable_id = _receivable(seed, db_session)
    client.post(f"/entries/{receivable_id}/close", headers=BOT)
    client.post(f"/entries/{receivable_id}/reopen", headers=BOT)
    assert client.delete(f"/entries/{receivable_id}", headers=BOT).status_code == 200

    assert _to_review(client, OWNER) == {"items": [], "total": 0}
    assert [event.action for event in _events(db_session)] == ["close", "reopen"]


def test_the_list_is_newest_first_with_a_limit_and_a_total(client, db_session, seed, scoped):
    ids = [_receivable(seed, db_session, who=who, name=who)[1] for who in ("Alan", "Bea", "Cal")]
    for entry_id in ids:
        client.post(f"/entries/{entry_id}/close", headers=BOT)

    page = _to_review(client, OWNER, limit=2)

    assert (page["total"], [item["entry_id"] for item in page["items"]]) == (3, [ids[2], ids[1]])
    assert client.get("/debt-events/to-review", headers=OWNER, params={"limit": 0}).status_code == 422
    assert client.get("/debt-events/to-review", headers=OWNER, params={"limit": 201}).status_code == 422


# --- the owner's review ----------------------------------------------------------------------------------------------


def _waiting(client, seed, db) -> tuple[int, int]:
    _, receivable_id = _receivable(seed, db, collected="200")
    event_id = client.post(f"/entries/{receivable_id}/close", headers=BOT).json()["debt_events"][0]["id"]
    return receivable_id, event_id


@pytest.mark.parametrize("headers", [OWNER, WEB], ids=["admin", "legacy"])
def test_the_owner_reviews_a_close_and_it_leaves_the_list(client, db_session, seed, scoped, headers):
    receivable_id, event_id = _waiting(client, seed, db_session)

    first = client.post(f"/debt-events/{event_id}/review", headers=headers)
    second = client.post(f"/debt-events/{event_id}/review", headers=headers)

    assert (first.status_code, second.status_code) == (204, 204)
    assert _to_review(client, OWNER) == {"items": [], "total": 0}
    [event] = _detail_events(client, receivable_id, OWNER)
    assert (event["id"], event["action"], event["needs_review"]) == (event_id, "close", False)
    [row] = _events(db_session)
    assert row.reviewed_at is not None and row.reviewed_at > row.created_at


@pytest.mark.parametrize("headers", [BOT, READER], ids=["write", "read"])
def test_a_caller_without_the_owner_scope_cannot_review(client, db_session, seed, scoped, headers):
    _, event_id = _waiting(client, seed, db_session)

    response = client.post(f"/debt-events/{event_id}/review", headers=headers)

    assert response.status_code == 403
    assert _to_review(client, OWNER)["total"] == 1
    assert _events(db_session)[0].reviewed_at is None


def test_review_of_an_unknown_event_or_of_a_reopen_is_refused(client, db_session, seed, scoped):
    receivable_id, _ = _waiting(client, seed, db_session)
    reopen_id = client.post(f"/entries/{receivable_id}/reopen", headers=BOT).json()["debt_events"][-1]["id"]

    assert client.post("/debt-events/999999/review", headers=OWNER).status_code == 404
    refused = client.post(f"/debt-events/{reopen_id}/review", headers=OWNER)
    assert refused.status_code == 422 and {str(e["loc"][-1]) for e in refused.json()["detail"]} == {"action"}
