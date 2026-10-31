"""Saved reports (reports PR A, task 7): CRUD, config validation, stale refs."""

from sqlalchemy import select

from app.models import SavedReport


def _config(**over) -> dict:
    return {"v": 1, "period": {"preset": "last_6_months"}, "metric": "expense", "group_by": "category",
            "filters": {}, **over}


def _body(name="報表", **over) -> dict:
    return {"name": name, "config": _config(), "chart": "bar", "pinned": False, **over}


def _create(client, **kwargs):
    response = client.post("/reports/saved", json=_body(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def _get(client, report_id) -> dict:
    response = client.get(f"/reports/saved/{report_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_save_relative_period_round_trips_preset(client, ledger):
    saved = _create(client)
    got = _get(client, saved["id"])
    assert got["config"] == _config()
    assert got["config"]["period"] == {"preset": "last_6_months"}
    assert got["stale_refs"] == []


def test_fixed_period_round_trips_with_from_key(client, ledger):
    config = _config(period={"from": "2026-01-01", "to": "2026-03-31"})
    saved = _create(client, config=config)
    assert _get(client, saved["id"])["config"]["period"] == {"from": "2026-01-01", "to": "2026-03-31"}


def test_weekend_food_config_round_trips(client, ledger):
    config = _config(filters={"category_id": [ledger.food], "weekday": [6, 7]}, split_by="subcategory",
                     compare="last_year")
    saved = _create(client, name="週末餐飲", config=config)
    got = _get(client, saved["id"])
    assert got["config"] == config
    assert got["stale_refs"] == []


def test_deleted_category_dropped_and_listed(client, db_session, ledger):
    fresh = client.post("/categories", json={"name": "臨時", "kind": "expense"}).json()["id"]
    saved = _create(client, config=_config(filters={"category_id": [ledger.food, fresh], "weekday": [6]}))
    assert client.delete(f"/categories/{fresh}").status_code == 204

    got = _get(client, saved["id"])
    assert got["config"]["filters"] == {"category_id": [ledger.food], "weekday": [6]}
    assert got["stale_refs"] == [f"category_id:{fresh}"]
    assert [r["stale_refs"] for r in client.get("/reports/saved").json()] == [[f"category_id:{fresh}"]]

    db_session.expire_all()
    stored = db_session.scalar(select(SavedReport.config).where(SavedReport.id == saved["id"]))
    assert fresh in stored["filters"]["category_id"]


def test_unknown_id_on_write_422(client, ledger):
    for key, value in [("category_id", [999999]), ("account_id", [999999]), ("project_id", [999999]),
                       ("counterparty_id", 999999)]:
        response = client.post("/reports/saved", json=_body(config=_config(filters={key: value})))
        assert response.status_code == 422, (key, response.text)
        assert f"unknown {key} 999999" in response.text
    assert client.get("/reports/saved").json() == []


def test_known_ids_accepted_on_write(client, ledger):
    filters = {"category_id": [ledger.food], "account_id": [ledger.wallet], "project_id": [ledger.trip]}
    assert client.post("/reports/saved", json=_body(config=_config(filters=filters))).status_code == 201


def test_invalid_config_422(client, ledger):
    response = client.post("/reports/saved", json=_body(config=_config(split_by="category")))
    assert response.status_code == 422
    assert client.post("/reports/saved", json=_body(name="")).status_code == 422
    assert client.post("/reports/saved", json=_body(name="x" * 41)).status_code == 422


def test_duplicate_name_409(client, ledger):
    first = _create(client, name="同名")
    assert client.post("/reports/saved", json=_body(name="同名")).status_code == 409
    other = _create(client, name="另一個")
    renamed = client.put(f"/reports/saved/{other['id']}", json=_body(name="同名"))
    assert renamed.status_code == 409
    same = client.put(f"/reports/saved/{first['id']}", json=_body(name="同名", pinned=True))
    assert same.status_code == 200


def test_unknown_report_404(client, ledger):
    assert client.get("/reports/saved/999999").status_code == 404
    assert client.put("/reports/saved/999999", json=_body()).status_code == 404
    assert client.delete("/reports/saved/999999").status_code == 404


def test_sort_order_default_and_reorder(client, ledger):
    # The ledger fixture leaves saved_report empty, so the defaults are absolute: first 0 (empty), second max + 1.
    a, b = _create(client, name="甲"), _create(client, name="乙")
    assert b["sort_order"] == a["sort_order"] + 1 and a["sort_order"] == 0
    assert [r["name"] for r in client.get("/reports/saved").json()] == ["甲", "乙"]

    moved = client.put(f"/reports/saved/{b['id']}", json=_body(name="乙", sort_order=-1))
    assert moved.status_code == 200 and moved.json()["sort_order"] == -1
    assert [r["name"] for r in client.get("/reports/saved").json()] == ["乙", "甲"]

    kept = client.put(f"/reports/saved/{b['id']}", json=_body(name="乙"))
    assert kept.json()["sort_order"] == -1


def test_pinned_flag_persists(client, ledger):
    saved = _create(client, pinned=True)
    assert _get(client, saved["id"])["pinned"] is True
    client.put(f"/reports/saved/{saved['id']}", json=_body(pinned=False))
    assert _get(client, saved["id"])["pinned"] is False


def test_put_replaces_and_delete_removes(client, ledger):
    saved = _create(client)
    changed = client.put(f"/reports/saved/{saved['id']}", json=_body(name="新名", chart="table",
                                                                       config=_config(metric="income")))
    assert changed.status_code == 200
    body = changed.json()
    assert (body["name"], body["chart"], body["config"]["metric"]) == ("新名", "table", "income")
    assert client.delete(f"/reports/saved/{saved['id']}").status_code == 204
    assert client.get(f"/reports/saved/{saved['id']}").status_code == 404
