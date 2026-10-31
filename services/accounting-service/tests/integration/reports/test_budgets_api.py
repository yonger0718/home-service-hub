"""Budgets (reports PR A, task 6): CRUD and GET /reports/budgets/status."""

from datetime import date

from sqlalchemy import select

from app.models import Account, Budget
from tests.helpers import make_entry


def _post(client, **body):
    return client.post("/reports/budgets", json=body)


def _status(client, month="2026-10"):
    response = client.get("/reports/budgets/status", params={"month": month})
    assert response.status_code == 200, response.text
    return response.json()


def _by_label(client, month="2026-10") -> dict:
    return {b["label"]: b for b in _status(client, month)["budgets"]}


def _rows(db_session):
    db_session.expire_all()
    return list(db_session.scalars(select(Budget).order_by(Budget.id)))


def _spend(db_session, ledger, amount, day, **fields):
    make_entry(db_session, db_session.get(Account, ledger.wallet), f"-{amount}", kind="expense",
               entry_date=day, **fields)
    db_session.commit()


def test_over_budget(client, db_session, ledger):
    _spend(db_session, ledger, "2390", date(2026, 10, 25), category_id=ledger.shopping)  # 1100 net + 2390
    assert _post(client, scope="category", category_id=ledger.shopping, amount="3000",
                 effective_from="2026-09").status_code == 201
    item = _by_label(client)["購物"]
    assert (item["limit"], item["used"], item["remaining"]) == ("3000.0000", "3490.0000", "-490.0000")
    assert item["over"] is True
    assert item["ratio"] == 1.1633
    assert item["color"] == "#3366ff"


def test_changing_amount_keeps_history(client, db_session, ledger):
    first = _post(client, scope="category", category_id=ledger.food, amount="10000", effective_from="2026-01")
    assert first.status_code == 201
    amended = client.put(f"/reports/budgets/{first.json()['id']}", json={"amount": "12000", "effective_from": "2026-11"})
    assert amended.status_code == 200
    assert amended.json()["limit"] == "12000.0000" and amended.json()["effective_from"] == "2026-11"
    assert _by_label(client, "2026-10")["餐飲"]["limit"] == "10000.0000"
    assert _by_label(client, "2026-11")["餐飲"]["limit"] == "12000.0000"
    assert len(_rows(db_session)) == 2
    again = client.put(f"/reports/budgets/{first.json()['id']}", json={"amount": "13000", "effective_from": "2026-11"})
    assert again.status_code == 200 and again.json()["id"] == amended.json()["id"]
    assert len(_rows(db_session)) == 2
    assert _by_label(client, "2026-11")["餐飲"]["limit"] == "13000.0000"


def test_month_window_is_half_open(client, db_session, ledger):
    _post(client, scope="total", amount="100000", effective_from="2026-01")
    before = _by_label(client)["全部支出"]["used"]
    _spend(db_session, ledger, "777", date(2026, 11, 1), category_id=ledger.shopping)
    _spend(db_session, ledger, "55", date(2026, 10, 31), category_id=ledger.shopping)
    assert _by_label(client)["全部支出"]["used"] == f"{float(before) + 55:.4f}"
    assert _by_label(client, "2026-11")["全部支出"]["used"] == "777.0000"


def test_project_budget_whole_life(client, db_session, ledger):
    assert client.put(f"/projects/{ledger.trip}", json={"name": "東京旅行", "exclude_from_reports": True}).status_code == 200
    response = _post(client, scope="project", project_id=ledger.trip, amount="30000")
    assert response.status_code == 201, response.text
    # 2860 + 22000 + JPY 5000 (unrated, left out) in October, 3000 in September
    for month in ("2026-10", "2026-09"):
        item = _by_label(client, month)["東京旅行"]
        assert item["used"] == "27860.0000" and item["limit"] == "30000.0000"
        assert item["effective_from"] is None and item["over"] is False and item["color"] is None


def test_project_put_updates_in_place(client, db_session, ledger):
    created = _post(client, scope="project", project_id=ledger.trip, amount="30000").json()
    response = client.put(f"/reports/budgets/{created['id']}", json={"amount": "35000", "effective_from": "2026-10"})
    assert response.status_code == 200, response.text
    rows = _rows(db_session)
    assert len(rows) == 1 and rows[0].effective_from is None and rows[0].amount == 35000
    assert response.json()["limit"] == "35000.0000" and response.json()["id"] == created["id"]


def test_total_budget_uses_report_rules(client, db_session, ledger):
    _post(client, scope="total", amount="50000", effective_from="2026-10")
    baseline = _by_label(client)["全部支出"]["used"]
    assert baseline == "29175.4684"  # fixture October expense (refund 490 already netted; fee included)
    client.put(f"/projects/{ledger.trip}", json={"name": "東京旅行", "exclude_from_reports": True})
    assert _by_label(client)["全部支出"]["used"] == "4315.4684"  # minus 2860 + 22000 (trip, TWD)


def test_time_progress(client, ledger, today):
    _post(client, scope="total", amount="1000", effective_from="2026-01")
    today(date(2026, 10, 15))
    assert _status(client, "2026-10")["time_progress"] == 0.4839
    assert _status(client, "2026-09")["time_progress"] == 1.0
    assert _status(client, "2026-11")["time_progress"] == 0.0


def test_delete_archives_identity_and_allows_recreate(client, db_session, ledger):
    first = _post(client, scope="category", category_id=ledger.food, amount="10000", effective_from="2026-01").json()
    client.put(f"/reports/budgets/{first['id']}", json={"amount": "12000", "effective_from": "2026-11"})
    assert client.delete(f"/reports/budgets/{first['id']}").status_code == 204
    assert "餐飲" not in _by_label(client, "2026-10") and "餐飲" not in _by_label(client, "2026-12")
    rows = _rows(db_session)
    assert len(rows) == 2 and all(row.archived for row in rows)
    assert client.delete(f"/reports/budgets/{first['id']}").status_code == 404
    assert client.put(f"/reports/budgets/{first['id']}", json={"amount": "1", "effective_from": "2026-12"}).status_code == 404
    again = _post(client, scope="category", category_id=ledger.food, amount="9000", effective_from="2026-01")
    assert again.status_code == 201
    assert _by_label(client, "2026-10")["餐飲"]["limit"] == "9000.0000"


def test_two_category_budgets_same_month(client, ledger):
    assert _post(client, scope="category", category_id=ledger.shopping, amount="3000",
                 effective_from="2026-10").status_code == 201
    assert _post(client, scope="category", category_id=ledger.food, amount="8000",
                 effective_from="2026-10").status_code == 201
    _post(client, scope="total", amount="40000", effective_from="2026-10")
    _post(client, scope="project", project_id=ledger.trip, amount="30000")
    budgets = _status(client)["budgets"]
    assert [b["label"] for b in budgets] == ["全部支出", "購物", "餐飲", "東京旅行"]


def test_create_conflicts_and_422s(client, ledger):
    assert _post(client, scope="category", category_id=ledger.food, amount="1", effective_from="2026-10").status_code == 201
    duplicate = _post(client, scope="category", category_id=ledger.food, amount="2", effective_from="2026-10")
    assert duplicate.status_code == 409
    assert duplicate.json()["message"] == "budget already starts in 2026-10"
    assert _post(client, scope="category", category_id=ledger.dinner, amount="1", effective_from="2026-10").status_code == 422
    assert _post(client, scope="category", category_id=ledger.food, amount="0", effective_from="2026-11").status_code == 422
    assert _post(client, scope="project", amount="5").status_code == 422
    assert _post(client, scope="total", amount="5").status_code == 422
    assert _post(client, scope="total", amount="5", effective_from="2026-13").status_code == 422
    assert _post(client, scope="project", project_id=99999, amount="5").status_code == 422
