"""Settings CRUD: account validation and flags, shared credit limits, groups, category tree, projects, counterparties."""

import uuid
from decimal import Decimal

from app.models import Account


def _account_body(**fields) -> dict:
    body = {"name": "新帳戶", "currency": "TWD"}
    body.update(fields)
    return body


def _error_fields(response) -> set[str]:
    return {str(error["loc"][-1]) for error in response.json()["detail"]}


def _category(client, name, kind="expense", **fields) -> dict:
    response = client.post("/categories", json={"kind": kind, "name": name, **fields})
    assert response.status_code == 201, response.text
    return response.json()


# --- accounts ---------------------------------------------------------------


def test_create_account_returns_detail_and_sets_the_local_flag(client, db_session):
    response = client.post(
        "/accounts", json=_account_body(icon="👛", note="日常", closing_day=25, fx_fee_pct="1.5", fx_fee_rounding="round")
    )

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["currency"], body["closing_day"], body["icon"], body["note"]) == (
        "新帳戶", "TWD", 25, "👛", "日常",
    )
    assert body["settings_locally_edited"] is True


def test_credit_only_fields_need_is_credit(client, db_session):
    # Spec: "Credit fields require the credit flag".
    refused = client.post("/accounts", json=_account_body(credit_limit="300000"))
    credit = client.post(
        "/accounts",
        json=_account_body(name="信用卡", is_credit=True, credit_limit="300000", due_rule="fixed_day", due_value=15, closing_day=25),
    )

    assert refused.status_code == 422 and _error_fields(refused) == {"credit_limit"}
    assert credit.status_code == 201
    assert client.post("/accounts", json=_account_body(name="半套", is_credit=True, due_rule="fixed_day")).status_code == 422


def test_closing_day_must_be_1_to_31(client, db_session):
    for day in (0, 32):
        response = client.post("/accounts", json=_account_body(closing_day=day))
        assert response.status_code == 422 and "closing_day" in _error_fields(response)


def test_combined_account_rules(client, db_session, seed):
    x = seed.account("X", is_credit=True)
    y = seed.account("Y", is_credit=True)
    archived = seed.account("舊卡", is_credit=True, is_archived=True)
    db_session.commit()
    x_id, y_id, archived_id = x.id, y.id, archived.id

    def put(account_id, name, **fields):
        return client.put(f"/accounts/{account_id}", json=_account_body(name=name, is_credit=True, **fields))

    self_ref = put(x_id, "X", combined_account_id=x_id)
    to_archived = put(x_id, "X", combined_account_id=archived_id)
    ok = put(x_id, "X", combined_account_id=y_id)
    cycle = put(y_id, "Y", combined_account_id=x_id)  # Spec: "Master account cycle refused".
    not_credit = client.put(f"/accounts/{y_id}", json=_account_body(name="Y", combined_account_id=x_id))

    assert self_ref.status_code == 422 and _error_fields(self_ref) == {"combined_account_id"}
    assert to_archived.status_code == 422 and _error_fields(to_archived) == {"combined_account_id"}
    assert ok.status_code == 200 and ok.json()["combined_account_id"] == y_id
    assert cycle.status_code == 422 and _error_fields(cycle) == {"combined_account_id"}
    assert not_credit.status_code == 422 and _error_fields(not_credit) == {"combined_account_id"}


def test_sub_card_under_an_archived_master_can_still_be_edited(client, db_session, seed):
    master = seed.account("主卡", is_credit=True)
    sub = seed.account("副卡", is_credit=True, combined_account_id=master.id)
    bank = seed.account("銀行")
    db_session.flush()
    sub.auto_pay_account_id = bank.id
    master.is_archived, bank.is_archived = True, True
    db_session.commit()
    sub_id, master_id, bank_id = sub.id, master.id, bank.id

    renamed = client.put(
        f"/accounts/{sub_id}",
        json=_account_body(name="副卡 改名", is_credit=True, combined_account_id=master_id, auto_pay_account_id=bank_id),
    )

    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["name"], renamed.json()["combined_account_id"]) == ("副卡 改名", master_id)


def test_archived_auto_pay_account_is_refused(client, db_session, seed):
    card = seed.account("信用卡", is_credit=True)
    old_bank = seed.account("舊銀行", is_archived=True)
    db_session.commit()
    card_id, old_bank_id = card.id, old_bank.id

    created = client.post("/accounts", json=_account_body(name="新卡", is_credit=True, auto_pay_account_id=old_bank_id))
    updated = client.put(f"/accounts/{card_id}", json=_account_body(name="信用卡", is_credit=True, auto_pay_account_id=old_bank_id))

    assert created.status_code == 422 and _error_fields(created) == {"auto_pay_account_id"}
    assert updated.status_code == 422 and _error_fields(updated) == {"auto_pay_account_id"}


def test_currency_frozen_once_entries_exist(client, db_session, seed):
    # Spec: "Currency frozen once entries exist".
    twd = seed.account("台幣帳戶")
    for amount in ("-1", "-2", "-3"):
        seed.entry(twd, amount)
    empty = seed.account("空帳戶")
    db_session.commit()

    frozen = client.put(f"/accounts/{twd.id}", json=_account_body(name="台幣帳戶", currency="JPY"))
    changed = client.put(f"/accounts/{empty.id}", json=_account_body(name="空帳戶", currency="JPY"))

    assert frozen.status_code == 422 and _error_fields(frozen) == {"currency"}
    assert changed.status_code == 200 and changed.json()["currency"] == "JPY"


def test_account_names_stay_unique(client, db_session, seed):
    seed.account("錢包")
    db_session.commit()

    response = client.post("/accounts", json=_account_body(name="錢包"))

    assert response.status_code == 422 and _error_fields(response) == {"name"}


def test_put_sets_the_local_flag_and_reset_clears_it(client, db_session, seed):
    imported = seed.account("MOZE 帳戶", moze_id="acc-9")
    seed.entry(imported, "-5", source="moze_backup", moze_id="rec-1")
    db_session.commit()
    account_id = imported.id

    edited = client.put(f"/accounts/{account_id}", json=_account_body(name="MOZE 帳戶", icon="💳", note="主要"))
    reset = client.post(f"/accounts/{account_id}/reset-settings-flag")

    assert edited.status_code == 200
    assert (edited.json()["icon"], edited.json()["note"], edited.json()["settings_locally_edited"]) == ("💳", "主要", True)
    assert reset.status_code == 200 and reset.json()["settings_locally_edited"] is False
    assert client.post("/accounts/999999/reset-settings-flag").status_code == 404


def test_account_with_entries_cannot_be_deleted(client, db_session, seed):
    # Spec: "Archive instead of delete".
    used = seed.account("用過")
    for _ in range(12):
        seed.entry(used, "-1")
    unused = seed.account("沒用過")
    db_session.commit()
    used_id, unused_id = used.id, unused.id

    refused = client.delete(f"/accounts/{used_id}")

    assert refused.status_code == 409 and "is_archived" in refused.json()["message"]
    assert client.delete(f"/accounts/{unused_id}").status_code == 204
    assert client.get(f"/accounts/{unused_id}").status_code == 404


# --- shared credit limit (額度共用) -----------------------------------------------


def _card_body(name, **fields) -> dict:
    return _account_body(name=name, is_credit=True, **fields)


def _sharing_ids(db_session, *account_ids) -> list:
    db_session.expire_all()
    return [db_session.get(Account, account_id).credit_sharing_id for account_id in account_ids]


def test_sharing_members_add_an_account_to_the_set(client, db_session, seed):
    shared = uuid.uuid4()
    a = seed.account("A", is_credit=True, credit_sharing_id=shared)
    b = seed.account("B", is_credit=True, credit_sharing_id=shared)
    c = seed.account("C", is_credit=True)
    cash = seed.account("現金")
    db_session.commit()
    a_id, b_id, c_id, cash_id = a.id, b.id, c.id, cash.id

    added = client.put(f"/accounts/{a_id}", json=_card_body("A", credit_sharing_members=[b_id, c_id]))
    refused = [
        client.put(f"/accounts/{a_id}", json=_card_body("A", credit_sharing_members=[a_id])),
        client.put(f"/accounts/{a_id}", json=_card_body("A", credit_sharing_members=[cash_id])),
        client.put(f"/accounts/{a_id}", json=_card_body("A", credit_sharing_members=[999999])),
        client.put(f"/accounts/{cash_id}", json=_account_body(name="現金", credit_sharing_members=[a_id])),
    ]

    assert added.status_code == 200
    assert (added.json()["credit_sharing_id"], added.json()["credit_sharing_members"]) == (str(shared), [b_id, c_id])
    assert _sharing_ids(db_session, a_id, b_id, c_id, cash_id) == [shared, shared, shared, None]
    assert client.get(f"/accounts/{c_id}").json()["credit_sharing_members"] == [a_id, b_id]
    for response in refused:
        assert response.status_code == 422 and _error_fields(response) == {"credit_sharing_members"}


def test_sharing_members_remove_an_account_from_the_set(client, db_session, seed):
    a, b, c = (seed.account(name, is_credit=True) for name in ("A", "B", "C"))
    db_session.commit()
    a_id, b_id, c_id = a.id, b.id, c.id

    created = client.put(f"/accounts/{a_id}", json=_card_body("A", credit_sharing_members=[b_id, c_id]))
    removed = client.put(f"/accounts/{a_id}", json=_card_body("A", credit_sharing_members=[b_id]))

    assert created.status_code == 200 and created.json()["credit_sharing_id"] is not None
    shared = uuid.UUID(created.json()["credit_sharing_id"])
    assert removed.status_code == 200 and removed.json()["credit_sharing_members"] == [b_id]
    assert _sharing_ids(db_session, a_id, b_id, c_id) == [shared, shared, None]
    assert client.get(f"/accounts/{c_id}").json()["credit_sharing_members"] == []
    assert client.get(f"/accounts/{b_id}").json()["credit_sharing_members"] == [a_id]


def test_empty_sharing_members_leave_the_set(client, db_session, seed):
    shared = uuid.uuid4()
    a, b, c = (seed.account(name, is_credit=True, credit_sharing_id=shared) for name in ("A", "B", "C"))
    db_session.commit()
    a_id, b_id, c_id = a.id, b.id, c.id

    left = client.put(f"/accounts/{a_id}", json=_card_body("A", credit_sharing_members=[]))
    untouched = client.put(f"/accounts/{b_id}", json=_card_body("B", credit_sharing_id=str(shared)))

    assert left.status_code == 200
    assert (left.json()["credit_sharing_id"], left.json()["credit_sharing_members"]) == (None, [])
    assert untouched.status_code == 200 and untouched.json()["credit_sharing_members"] == [c_id]
    assert _sharing_ids(db_session, a_id, b_id, c_id) == [None, shared, shared]


def test_sharing_edit_marks_every_touched_account(client, db_session, seed):
    # Plan review round 2: a re-import must keep the local sharing on the members too, not only on the edited card.
    shared = uuid.uuid4()
    a, b, c = (
        seed.account(name, is_credit=True, credit_sharing_id=shared, settings_locally_edited=False)
        for name in ("A", "B", "C")
    )
    d = seed.account("D", is_credit=True, settings_locally_edited=False)
    db_session.commit()
    a_id, b_id, c_id, d_id = a.id, b.id, c.id, d.id

    response = client.put(f"/accounts/{a_id}", json=_card_body("A", credit_sharing_members=[b_id, d_id]))

    assert response.status_code == 200
    assert _sharing_ids(db_session, a_id, b_id, c_id, d_id) == [shared, shared, None, shared]
    flags = [db_session.get(Account, i).settings_locally_edited for i in (a_id, b_id, c_id, d_id)]
    assert flags == [True, False, True, True]  # A edited, B unchanged, C removed, D added


def test_account_detail_keeps_as_of_after_sharing_fields(client, db_session, seed):
    # Plan review round 3: Task 4's router calls get_account(db, account_id, as_of=as_of) on every request.
    shared = uuid.uuid4()
    a = seed.account("A", is_credit=True, credit_sharing_id=shared)
    b = seed.account("B", is_credit=True, credit_sharing_id=shared)
    db_session.commit()
    a_id, b_id = a.id, b.id

    response = client.get(f"/accounts/{a_id}", params={"as_of": "2026-09-01"})

    assert response.status_code == 200
    assert response.json()["credit_sharing_members"] == [b_id]


# --- account groups ------------------------------------------------------------


def test_account_groups_crud_order_and_delete_rule(client, db_session, seed):
    # Spec: "Group with accounts cannot be deleted".
    card = client.post("/account-groups", json={"name": "信用卡"}).json()
    cash = client.post("/account-groups", json={"name": "現金", "sort_order": 1}).json()
    duplicate = client.post("/account-groups", json={"name": "現金"})
    for index in range(3):
        seed.account(f"卡{index}", group_id=card["id"])
    db_session.commit()

    refused = client.delete(f"/account-groups/{card['id']}")
    ordered = client.put("/account-groups/order", json={"ids": [cash["id"], card["id"]]})
    renamed = client.put(f"/account-groups/{cash['id']}", json={"name": "現金與錢包", "sort_order": 0})

    assert duplicate.status_code == 422 and _error_fields(duplicate) == {"name"}
    assert refused.status_code == 409
    assert [group["name"] for group in ordered.json()] == ["現金", "信用卡"]
    assert renamed.status_code == 200 and renamed.json()["name"] == "現金與錢包"
    assert [group["name"] for group in client.get("/account-groups").json()] == ["現金與錢包", "信用卡"]
    assert client.delete(f"/account-groups/{cash['id']}").status_code == 204
    assert client.put("/account-groups/order", json={"ids": [999999]}).status_code == 422


# --- categories ------------------------------------------------------------------


def test_category_tree_inherits_icon_and_colour(client, db_session):
    # Spec: "Sub-category inherits icon and colour" and "Same sub-category name under different main categories".
    food = _category(client, "飲食", icon="🍜", color="#f0cd92")
    social = _category(client, "社交", sort_order=1)
    lunch = _category(client, "午餐", parent_id=food["id"])
    _category(client, "午餐", parent_id=social["id"])

    tree = client.get("/categories", params={"kind": "expense"}).json()

    assert (lunch["icon"], lunch["color"]) == ("🍜", "#f0cd92")
    assert [(main["name"], [child["name"] for child in main["children"]]) for main in tree] == [
        ("飲食", ["午餐"]), ("社交", ["午餐"]),
    ]
    assert (tree[0]["children"][0]["icon"], tree[0]["children"][0]["color"]) == ("🍜", "#f0cd92")
    assert (tree[1]["children"][0]["icon"], tree[1]["children"][0]["parent_id"]) == (None, social["id"])
    assert client.get("/categories", params={"kind": "income"}).json() == []


def test_category_write_rules(client, db_session):
    food = _category(client, "飲食")
    lunch = _category(client, "午餐", parent_id=food["id"])

    third_level = client.post("/categories", json={"kind": "expense", "name": "便當", "parent_id": lunch["id"]})
    system_kind = client.post("/categories", json={"kind": "fee", "name": "其他費用"})
    duplicate = client.post("/categories", json={"kind": "expense", "name": "飲食"})
    wrong_parent = client.post("/categories", json={"kind": "income", "name": "獎金", "parent_id": food["id"]})
    hidden = client.put(
        f"/categories/{lunch['id']}", json={"kind": "expense", "name": "午餐", "parent_id": food["id"], "is_hidden": True}
    )
    kind_change = client.put(f"/categories/{lunch['id']}", json={"kind": "income", "name": "午餐"})
    demote_parent = client.put(
        f"/categories/{food['id']}", json={"kind": "expense", "name": "飲食", "parent_id": _category(client, "其他")["id"]}
    )

    assert _error_fields(third_level) == {"parent_id"}
    assert _error_fields(system_kind) == {"kind"}
    assert _error_fields(duplicate) == {"name"}
    assert _error_fields(wrong_parent) == {"parent_id"}
    assert hidden.status_code == 200 and hidden.json()["is_hidden"] is True
    assert _error_fields(kind_change) == {"kind"}
    assert _error_fields(demote_parent) == {"parent_id"}


def test_category_delete_only_when_unused(client, db_session, seed):
    food = _category(client, "飲食")
    lunch = _category(client, "午餐", parent_id=food["id"])
    snack = _category(client, "點心")
    seed.entry(seed.account(), "-100", category_id=lunch["id"])
    db_session.commit()

    assert client.delete(f"/categories/{lunch['id']}").status_code == 409
    assert client.delete(f"/categories/{food['id']}").status_code == 409
    assert client.delete(f"/categories/{snack['id']}").status_code == 204
    assert client.delete(f"/categories/{snack['id']}").status_code == 404


def test_categories_reorder(client, db_session):
    first, second, third = (_category(client, name)["id"] for name in ("甲", "乙", "丙"))

    response = client.put("/categories/order", json={"ids": [third, first, second]})

    assert response.status_code == 200
    assert [main["id"] for main in client.get("/categories", params={"kind": "expense"}).json()] == [third, first, second]
    assert client.put("/categories/order", json={"ids": [999999]}).status_code == 422


# --- projects ----------------------------------------------------------------------


def test_projects_crud_and_delete_rule(client, db_session, seed):
    trip = client.post("/projects", json={"name": "日本行"}).json()
    life = client.post("/projects", json={"name": "生活", "sort_order": 1}).json()
    duplicate = client.post("/projects", json={"name": "生活"})
    archived = client.put(f"/projects/{trip['id']}", json={"name": "日本行", "is_archived": True})
    seed.entry(seed.account(), "-1", project_id=life["id"])
    db_session.commit()

    assert duplicate.status_code == 422 and _error_fields(duplicate) == {"name"}
    assert archived.status_code == 200 and archived.json()["is_archived"] is True
    assert [project["name"] for project in client.get("/projects").json()] == ["生活", "日本行"]
    assert client.delete(f"/projects/{life['id']}").status_code == 409
    assert client.delete(f"/projects/{trip['id']}").status_code == 204


# --- counterparties ------------------------------------------------------------------


def test_counterparty_open_amounts_per_currency(client, db_session, seed):
    # Spec: "Open amount per counterparty" and "Currencies kept apart".
    card, wallet, yen = seed.account("卡"), seed.account("錢包"), seed.account("日幣", currency="JPY")
    alan, bob = seed.counterparty("Alan"), seed.counterparty("Bob")
    loan = seed.entry(card, "-420", kind="receivable", counterparty_id=alan.id)
    seed.entry(wallet, "200", kind="receivable", counterparty_id=alan.id, settles_entry_id=loan.id, is_settlement=True)
    seed.entry(wallet, "-50", kind="expense")
    db_session.commit()
    alan_id = alan.id

    first = {row["name"]: row for row in client.get("/counterparties").json()}
    seed.entry(yen, "1000", kind="payable", counterparty_id=alan_id)
    db_session.commit()
    second = {row["name"]: row for row in client.get("/counterparties").json()}

    assert first["Alan"]["open_amounts"] == [{"currency": "TWD", "amount": "220.0000"}]
    assert first["Bob"]["open_amounts"] == []
    assert second["Alan"]["open_amounts"] == [
        {"currency": "JPY", "amount": "-1000.0000"}, {"currency": "TWD", "amount": "220.0000"},
    ]


def test_counterparty_rename_shows_in_listings_and_delete_rule(client, db_session, seed):
    wallet = seed.account()
    alan, spare = seed.counterparty("Alan"), seed.counterparty("Spare")
    seed.entry(wallet, "-100", kind="receivable", counterparty_id=alan.id)
    db_session.commit()
    wallet_id, alan_id, spare_id = wallet.id, alan.id, spare.id

    renamed = client.put(f"/counterparties/{alan_id}", json={"name": "Alan Chen"})
    [item] = client.get(f"/accounts/{wallet_id}/entries").json()["items"]
    duplicate = client.post("/counterparties", json={"name": "Spare"})
    created = client.post("/counterparties", json={"name": "Carol"})

    assert renamed.status_code == 200 and renamed.json()["name"] == "Alan Chen"
    assert item["counterparty"] == "Alan Chen"
    assert duplicate.status_code == 422 and _error_fields(duplicate) == {"name"}
    assert created.status_code == 201 and created.json()["open_amounts"] == []
    assert client.delete(f"/counterparties/{alan_id}").status_code == 409
    assert client.delete(f"/counterparties/{spare_id}").status_code == 204


def test_sort_order_and_ids_beyond_int32_are_422(client, db_session):
    assert client.post("/projects", json={"name": "大", "sort_order": 2**31}).status_code == 422
    assert client.post("/account-groups", json={"name": "大", "sort_order": -(2**31) - 1}).status_code == 422
    for route in ("/categories/order", "/account-groups/order"):
        reorder = client.put(route, json={"ids": [2**31]})
        assert reorder.status_code == 422, route
        assert any(error["loc"][:2] == ["body", "ids"] for error in reorder.json()["detail"]), route
    assert client.post("/accounts", json=_account_body(group_id=2**31)).status_code == 422


def test_deleting_an_account_flags_the_accounts_whose_links_it_clears(client, db_session, seed):
    master = seed.account("主卡", is_credit=True)
    sub = seed.account("副卡", is_credit=True, combined_account_id=master.id)
    payer = seed.account("自動扣繳", is_credit=True, auto_pay_account_id=master.id)
    bystander = seed.account("無關")
    db_session.commit()
    ids = (sub.id, payer.id, bystander.id)

    assert client.delete(f"/accounts/{master.id}").status_code == 204

    db_session.expire_all()
    flags = [db_session.get(Account, account_id).settings_locally_edited for account_id in ids]
    assert flags == [True, True, False]


def test_a_row_missing_from_the_reread_listing_is_404_not_500(client, db_session, monkeypatch):
    from app.services import settings_service

    monkeypatch.setattr(settings_service, "list_groups", lambda db: [])  # e.g. deleted concurrently after the commit

    response = client.post("/account-groups", json={"name": "銀行"})

    assert response.status_code == 404
