"""Row statistics (≤ 50 groups) and tag overlap of GET /reports/breakdown."""

from datetime import date
from decimal import Decimal

from app.models import Account
from tests.helpers import make_entry

OCTOBER = {"date_from": "2026-10-01", "date_to": "2026-10-31"}
COFFEE = [65, 70, 72, 80, 85, 88, 90, 95, 100, 105, 110, 120]  # 12 entries, 12 distinct days


def _breakdown(client, **params) -> dict:
    response = client.get("/reports/breakdown", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _rows(body) -> dict:
    return {row["key"]: row for row in body["rows"]}


def test_coffee_statistics(client, db_session, ledger):
    wallet = db_session.get(Account, ledger.wallet)
    for index, amount in enumerate(COFFEE):
        make_entry(db_session, wallet, str(-amount), merchant="路易莎", entry_date=date(2026, 10, 1 + 2 * index))
    db_session.commit()

    row = _rows(_breakdown(client, **OCTOBER, group_by="merchant", merchant="路易莎"))["路易莎"]

    assert row["count"] == 12
    assert Decimal(row["min"]) == 65 and Decimal(row["max"]) == 120
    assert Decimal(row["median"]) == Decimal(88 + 90) / 2
    assert (row["first_date"], row["last_date"]) == ("2026-10-01", "2026-10-23")
    assert row["avg_interval_days"] == round(22 / 11, 1)


def test_stats_null_above_50_groups(client, db_session, ledger):
    wallet = db_session.get(Account, ledger.wallet)
    for i in range(51):
        make_entry(db_session, wallet, "-10", merchant=f"攤{i:03d}", entry_date=date(2026, 10, 16))
    db_session.commit()

    body = _breakdown(client, **OCTOBER, group_by="merchant", merchant="攤")

    assert len(body["rows"]) == 51
    for row in body["rows"]:
        assert row["median"] is None and row["min"] is None and row["avg_interval_days"] is None
        assert Decimal(row["avg"]) == 10


def test_tag_overlap(client, ledger):
    body = _breakdown(client, **OCTOBER, group_by="tag")
    rows = _rows(body)

    assert Decimal(rows["朋友"]["amount"]) == 600 and Decimal(rows["生日"]["amount"]) == 600
    assert Decimal(body["totals"]["expense"]) == Decimal("29175.4684")
    assert body["overlapping"] is True
    assert rows["朋友"]["median"] is not None
    assert _breakdown(client, **OCTOBER, group_by="category")["overlapping"] is False


def test_split_by_tag_does_not_multiply_the_row(client, ledger):
    body = _breakdown(client, **OCTOBER, group_by="category", split_by="tag")
    food = _rows(body)[str(ledger.food)]
    splits = {s["key"]: s for s in food["splits"]}

    assert Decimal(splits["朋友"]["amount"]) == 600 and Decimal(splits["生日"]["amount"]) == 600
    assert Decimal(food["amount"]) == Decimal("5676.4057")
    assert Decimal(body["totals"]["expense"]) == Decimal("29175.4684")
    assert body["overlapping"] is True


def test_tag_rows_without_tags_form_null_row(client, ledger):
    rows = _rows(_breakdown(client, **OCTOBER, group_by="tag"))

    assert rows[None]["label"] == ""
    # untagged rated spend rows: 120 95 1590 130 450 Uber Starbucks, the 490 refund, the 15 fee
    assert rows[None]["count"] == 9


def test_split_by_tag_null_split(client, ledger):
    body = _breakdown(client, **OCTOBER, group_by="category", split_by="tag")
    transport = _rows(body)[str(ledger.transport)]
    shopping = _rows(body)[str(ledger.shopping)]

    # 交通: 長榮航空 is tagged 旅行, the Uber row is not
    assert {s["key"]: s["label"] for s in transport["splits"]} == {"旅行": "旅行", None: ""}
    assert [(s["key"], s["label"], s["count"]) for s in shopping["splits"]] == [(None, "", 2)]
