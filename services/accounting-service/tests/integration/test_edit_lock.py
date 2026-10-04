"""Review Focus 2 across every write endpoint: MOZE rows answer 409 locked_until_cutover until
ACCOUNTING_IMPORT_LOCKED=true; account settings and new manual rows are always allowed."""

import uuid

import pytest
from sqlalchemy import select

from app.main import app
from app.models import Account, EntryGroup, LedgerEntry

DAY = "2026-09-10"


@pytest.fixture()
def books(seed, db_session) -> dict:
    """Imported rows of every shape a write endpoint can target (synthetic)."""
    imported = {"source": "moze_backup"}
    wallet = seed.account("錢包", opening="1000", moze_id="acc-1")
    bank = seed.account("銀行", moze_id="acc-2")
    alan = seed.counterparty("Alan")
    expense = seed.entry(wallet, "-1200", moze_id="rec-1", **imported)
    receivable = seed.entry(wallet, "-420", kind="receivable", counterparty_id=alan.id, moze_id="rec-2", **imported)
    transfer = uuid.uuid4()
    out_leg = seed.entry(wallet, "-300", kind="transfer_out", transfer_group_id=transfer, moze_id="rec-3", **imported)
    seed.entry(bank, "300", kind="transfer_in", transfer_group_id=transfer, moze_id="rec-4", **imported)
    split = seed.group(moze_id="pkg-1", name="聚餐")
    member = seed.entry(wallet, "-60", group_id=split.id, moze_id="rec-5", **imported)
    seed.entry(bank, "-40", group_id=split.id, moze_id="rec-6", **imported)
    manual = seed.entry(wallet, "-10")
    db_session.commit()
    return {
        "wallet": wallet.id, "bank": bank.id, "expense": expense.id, "receivable": receivable.id,
        "transfer": str(transfer), "out_leg": out_leg.id, "split": split.id, "member": member.id, "manual": manual.id,
    }


def _entry_body(books: dict, **fields) -> dict:
    body = {"account_id": books["wallet"], "kind": "expense", "amount": "80", "entry_date": DAY}
    body.update(fields)
    return body


CASES = {
    "put_entry": lambda b: ("PUT", f"/entries/{b['expense']}", _entry_body(b), 200),
    "delete_entry": lambda b: ("DELETE", f"/entries/{b['expense']}", None, 204),
    "put_group_member": lambda b: ("PUT", f"/entries/{b['member']}", _entry_body(b), 200),
    "settle": lambda b: (
        "POST", f"/entries/{b['receivable']}/settle", {"account_id": b["wallet"], "amount": "100", "entry_date": DAY}, 201,
    ),
    "refund": lambda b: ("POST", f"/entries/{b['expense']}/refund", {"amount": "50", "entry_date": DAY}, 201),
    "refund_group_member": lambda b: ("POST", f"/entries/{b['member']}/refund", {"amount": "10", "entry_date": DAY}, 201),
    "put_transfer": lambda b: (
        "PUT", f"/transfers/{b['transfer']}",
        {"from_account_id": b["wallet"], "to_account_id": b["bank"], "out_amount": "250", "entry_date": DAY}, 200,
    ),
    "delete_transfer_leg": lambda b: ("DELETE", f"/entries/{b['out_leg']}", None, 204),
    "put_split": lambda b: ("PUT", f"/splits/{b['split']}", {"entry_date": DAY, "members": [_entry_body(b, amount="40")]}, 200),
    "delete_split": lambda b: ("DELETE", f"/splits/{b['split']}", None, 204),
}


def _snapshot(db) -> list[tuple]:
    db.expire_all()
    entries = db.execute(
        select(
            LedgerEntry.id, LedgerEntry.amount, LedgerEntry.source, LedgerEntry.account_id,
            LedgerEntry.settles_entry_id, LedgerEntry.refunds_entry_id, LedgerEntry.group_id,
        ).order_by(LedgerEntry.id)
    ).all()
    groups = db.execute(select(EntryGroup.id, EntryGroup.name).order_by(EntryGroup.id)).all()
    return [tuple(row) for row in entries] + [tuple(row) for row in groups]


@pytest.mark.parametrize("case", sorted(CASES))
def test_write_on_a_moze_row_is_refused_before_cutover(client, books, case):
    method, path, body, _ = CASES[case](books)

    response = client.request(method, path, json=body)

    assert (response.status_code, response.json()["message"]) == (409, "locked_until_cutover")


def test_refused_writes_change_nothing(client, db_session, books):
    before = _snapshot(db_session)

    for case in CASES.values():
        method, path, body, _ = case(books)
        assert client.request(method, path, json=body).status_code == 409

    assert _snapshot(db_session) == before


@pytest.mark.parametrize("case", sorted(CASES))
def test_write_on_a_moze_row_succeeds_after_cutover(client, books, case, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    method, path, body, expected = CASES[case](books)

    response = client.request(method, path, json=body)

    assert response.status_code == expected, response.text


@pytest.mark.parametrize(
    "marks",
    [{"source": "moze_import"}, {"source": "moze_backup"}, {"source": "manual", "moze_id": "rec-77"}],
    ids=["moze_import", "moze_backup", "moze_id_only"],
)
def test_every_moze_marker_locks(client, db_session, seed, marks):
    wallet = seed.account("錢包")
    entry = seed.entry(wallet, "-100", **marks)
    db_session.commit()

    response = client.put(
        f"/entries/{entry.id}", json={"account_id": wallet.id, "kind": "expense", "amount": "1", "entry_date": DAY}
    )

    assert (response.status_code, response.json()["message"]) == (409, "locked_until_cutover")


def test_account_settings_are_editable_on_an_imported_account_while_unlocked(client, db_session, books):
    response = client.put(
        f"/accounts/{books['wallet']}",
        json={"name": "錢包", "currency": "TWD", "opening_balance": "1000", "icon": "👛", "closing_day": 5},
    )

    assert response.status_code == 200
    db_session.expire_all()
    account = db_session.get(Account, books["wallet"])
    assert (account.icon, account.closing_day, account.settings_locally_edited, account.moze_id) == (
        "👛", 5, True, "acc-1",
    )


def test_manual_rows_stay_writable_while_unlocked(client, db_session, books):
    created = client.post("/entries", json=_entry_body(books))
    edited = client.put(f"/entries/{books['manual']}", json=_entry_body(books, amount="12"))
    deleted = client.delete(f"/entries/{created.json()['id']}")

    assert created.status_code == 201
    assert edited.status_code == 200 and edited.json()["amount"] == "-12.0000"
    assert deleted.status_code == 204


EXPECTED_WRITES = {
    ("/entries", "POST"), ("/entries/{entry_id}", "PUT"), ("/entries/{entry_id}", "DELETE"),
    ("/entries/{entry_id}/settle", "POST"), ("/entries/{entry_id}/refund", "POST"),
    ("/transfers", "POST"), ("/transfers/{group_id}", "PUT"),
    ("/splits", "POST"), ("/splits/{group_id}", "PUT"), ("/splits/{group_id}", "DELETE"),
    ("/balance-adjustments", "POST"),
    ("/accounts", "POST"), ("/accounts/{account_id}", "PUT"), ("/accounts/{account_id}", "DELETE"),
    ("/accounts/{account_id}/reset-settings-flag", "POST"),
    ("/account-groups", "POST"), ("/account-groups/order", "PUT"),
    ("/account-groups/{group_id}", "PUT"), ("/account-groups/{group_id}", "DELETE"),
    ("/categories", "POST"), ("/categories/order", "PUT"),
    ("/categories/{category_id}", "PUT"), ("/categories/{category_id}", "DELETE"),
    ("/projects", "POST"), ("/projects/{project_id}", "PUT"), ("/projects/{project_id}", "DELETE"),
    ("/counterparties", "POST"), ("/counterparties/{counterparty_id}", "PUT"),
    ("/counterparties/{counterparty_id}", "DELETE"),
    ("/preference", "PUT"),
    ("/imports/moze", "POST"), ("/imports/moze-backup", "POST"),
    # Schedules: PUT / DELETE of an imported definition answer locked_until_cutover (schedule_service, D36).
    ("/schedules/definitions", "POST"), ("/schedules/definitions/{definition_id}", "PUT"),
    ("/schedules/definitions/{definition_id}", "DELETE"),
    # State actions are allowed on imported definitions before cutover (spec "Imported definitions before cutover").
    ("/schedules/definitions/{definition_id}/pause", "POST"), ("/schedules/definitions/{definition_id}/resume", "POST"),
    ("/schedules/definitions/{definition_id}/end", "POST"), ("/schedules/definitions/{definition_id}/mode", "PUT"),
    ("/schedules/definitions/{definition_id}/catch-up", "POST"),
    # Instance actions; repost / reopen of a MOZE-booked period answer locked_until_cutover (spec "Instance endpoints").
    ("/schedules/instances/{instance_id}", "PUT"), ("/schedules/instances/{instance_id}/post", "POST"),
    ("/schedules/instances/{instance_id}/skip", "POST"), ("/schedules/instances/{instance_id}/reopen", "POST"),
    ("/schedules/instances/{instance_id}/repost", "POST"),
    ("/schedules/instances/{instance_id}/accept-partial", "POST"),
    # The daily job, now (acted_by auto; the job posts without the cutover check, D34).
    ("/schedules/run-now", "POST"),
}


def test_write_routes_inventory():
    writes = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set()) & {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert writes == EXPECTED_WRITES
