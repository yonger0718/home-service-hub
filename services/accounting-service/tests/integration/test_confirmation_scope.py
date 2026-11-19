"""#396 / #390: the confirming parameters (`?drop_children=true` on the four update routes, `?cascade=true` on
DELETE /entries/{id}) need the `admin` scope, so a caller that only holds `write` (the assistant) cannot confirm
on its own; `legacy` (the SPA) and `admin` pass. Fixtures are fictional."""

from datetime import date

import pytest
from sqlalchemy import select

from app import auth
from app.models import LedgerEntry

DAY = date(2026, 9, 1)
ADMIN = {"Authorization": "Bearer adm-token"}
WRITE = {"Authorization": "Bearer wr-token"}
SPA = {"Authorization": "Bearer spa-token"}


@pytest.fixture
def scoped(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, "adm:adm-token,wr:wr-token,spa:spa-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "adm=read,write,admin; wr=read,write; spa=legacy")


def _entry_body(account_id, **fields) -> dict:
    body = {"account_id": account_id, "kind": "expense", "amount": "100", "entry_date": DAY.isoformat()}
    body.update(fields)
    return body


def _member(account_id, **fields) -> dict:
    body = {"account_id": account_id, "kind": "expense", "amount": "100"}
    body.update(fields)
    return body


def _split(*members) -> dict:
    return {"entry_date": DAY.isoformat(), "members": list(members)}


def _fee_of(db, parent_id) -> LedgerEntry | None:
    db.expire_all()
    return db.scalar(select(LedgerEntry).where(LedgerEntry.parent_entry_id == parent_id, LedgerEntry.kind == "fee"))


def _entry_case(client, seed, db):
    wallet = seed.account("A", opening="1000")
    db.commit()
    entry_id = client.post("/entries", headers=ADMIN, json=_entry_body(wallet.id, fee={"amount": "15"})).json()["id"]
    return f"/entries/{entry_id}", _entry_body(wallet.id, name="午餐"), entry_id


def _convert_case(client, seed, db):
    url, body, entry_id = _entry_case(client, seed, db)
    account_id = body["account_id"]
    return f"{url}/split", _split(_member(account_id, id=entry_id, amount="60"), _member(account_id, amount="40")), entry_id


def _transfer_case(client, seed, db):
    a, b = seed.account("A", opening="5000"), seed.account("B")
    db.commit()
    body = {"from_account_id": a.id, "to_account_id": b.id, "out_amount": "3000", "entry_date": DAY.isoformat()}
    created = client.post("/transfers", headers=ADMIN, json={**body, "out_fee": {"amount": "10"}}).json()
    return f"/transfers/{created['transfer_group_id']}", {**body, "out_amount": "2000"}, created["out_entry_id"]


def _split_case(client, seed, db):
    wallet = seed.account("A", opening="1000")
    db.commit()
    created = client.post("/splits", headers=ADMIN, json=_split(
        _member(wallet.id, fee={"amount": "15"}), _member(wallet.id, amount="50"))).json()
    first, second = created["member_ids"]
    body = _split(_member(wallet.id, id=first, amount="80"), _member(wallet.id, id=second, amount="50"))
    return f"/splits/{created['group_id']}", body, first


CASES = {"entry": _entry_case, "convert": _convert_case, "transfer": _transfer_case, "split": _split_case}


@pytest.mark.parametrize("case", sorted(CASES))
def test_write_scope_cannot_confirm_a_drop(client, db_session, seed, scoped, case):
    url, body, parent_id = CASES[case](client, seed, db_session)
    fee_id = _fee_of(db_session, parent_id).id

    response = client.put(f"{url}?drop_children=true", headers=WRITE, json=body)

    assert response.status_code == 403, response.text
    assert _fee_of(db_session, parent_id).id == fee_id


@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize("headers", [ADMIN, SPA], ids=["admin", "legacy"])
def test_admin_and_legacy_scopes_confirm_a_drop(client, db_session, seed, scoped, case, headers):
    url, body, parent_id = CASES[case](client, seed, db_session)

    response = client.put(f"{url}?drop_children=true", headers=headers, json=body)

    assert response.status_code == 200, response.text
    assert _fee_of(db_session, parent_id) is None


@pytest.mark.parametrize("case", sorted(CASES))
def test_write_scope_is_refused_the_drop_by_its_own_409_without_the_parameter(client, db_session, seed, scoped, case):
    url, body, parent_id = CASES[case](client, seed, db_session)

    for suffix in ("", "?drop_children=false"):
        response = client.put(f"{url}{suffix}", headers=WRITE, json=body)
        assert response.status_code == 409, response.text
        assert response.json()["message"].startswith("children_unconfirmed: ")
    assert _fee_of(db_session, parent_id) is not None


def _foreign_child(seed, db) -> tuple[int, int]:
    """#390's shape: an income on one account with a fee row on another."""
    a, b = seed.account("收款戶"), seed.account("卡", is_credit=True)
    receipt = seed.entry(a, "600", kind="income")
    fee = seed.entry(b, "-9", kind="fee", parent_entry_id=receipt.id)
    db.commit()
    return receipt.id, fee.id


def test_write_scope_cannot_confirm_a_cascade(client, db_session, seed, scoped):
    receipt_id, fee_id = _foreign_child(seed, db_session)

    response = client.delete(f"/entries/{receipt_id}?cascade=true", headers=WRITE)

    assert response.status_code == 403, response.text
    db_session.expire_all()
    assert db_session.get(LedgerEntry, receipt_id) is not None and db_session.get(LedgerEntry, fee_id) is not None


@pytest.mark.parametrize("headers", [ADMIN, SPA], ids=["admin", "legacy"])
def test_admin_and_legacy_scopes_confirm_a_cascade(client, db_session, seed, scoped, headers):
    receipt_id, fee_id = _foreign_child(seed, db_session)

    response = client.delete(f"/entries/{receipt_id}?cascade=true", headers=headers)

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(LedgerEntry, receipt_id) is None and db_session.get(LedgerEntry, fee_id) is None


def test_write_scope_still_updates_and_deletes_without_confirming(client, db_session, seed, scoped):
    url, body, entry_id = _entry_case(client, seed, db_session)
    fee_id = _fee_of(db_session, entry_id).id

    kept = client.put(url, headers=WRITE, json={**body, "fee": {"amount": "15"}})
    explicit = client.put(f"{url}?drop_children=false", headers=WRITE, json={**body, "fee": {"amount": "15"}})

    assert (kept.status_code, explicit.status_code) == (200, 200), (kept.text, explicit.text)
    assert _fee_of(db_session, entry_id).id == fee_id

    deleted = client.delete(f"{url}?cascade=false", headers=WRITE)

    assert deleted.status_code == 200, deleted.text
    assert [row["role"] for row in deleted.json()["deleted"]] == ["entry", "child"]


def test_refusal_details_do_not_name_the_parameter(client, db_session, seed, scoped):
    url, body, _ = _entry_case(client, seed, db_session)
    receipt_id, _ = _foreign_child(seed, db_session)

    drop = client.put(url, headers=WRITE, json=body)
    cascade = client.delete(f"/entries/{receipt_id}", headers=WRITE)

    assert (drop.status_code, cascade.status_code) == (409, 409), (drop.text, cascade.text)
    for message, code in ((drop.json()["message"], "children_unconfirmed"), (cascade.json()["message"], "cascade_unconfirmed")):
        assert message.startswith(f"{code}: ")
        detail = message.removeprefix(f"{code}: ")
        assert "=true" not in detail and "drop_children" not in detail and "cascade" not in detail
        assert "the owner confirms" in detail


def test_unconfigured_scopes_keep_confirming(client, db_session, seed):
    """Compatibility mode (no scopes configured, as in most of the suite): the parameter alone confirms."""
    wallet = seed.account("A", opening="1000")
    db_session.commit()
    entry_id = client.post("/entries", json=_entry_body(wallet.id, fee={"amount": "15"})).json()["id"]

    response = client.put(f"/entries/{entry_id}?drop_children=true", json=_entry_body(wallet.id))

    assert response.status_code == 200, response.text
    assert _fee_of(db_session, entry_id) is None
