"""GET /reports/breakdown: totals, rows per dimension, splits, compare, excluded projects, the 200-row fold."""

from datetime import date
from decimal import Decimal

from app.models import Account
from tests.helpers import make_entry

OCTOBER = {"date_from": "2026-10-01", "date_to": "2026-10-31"}
SEPTEMBER = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


def _breakdown(client, **params) -> dict:
    response = client.get("/reports/breakdown", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _rows(body) -> dict:
    return {row["key"]: row for row in body["rows"]}


def test_totals_match_month_summary_exactly(client, ledger):
    summary = client.get("/entries/summary", params={"month": "2026-10"}).json()
    body = _breakdown(client, **OCTOBER, group_by="category")

    assert Decimal(summary["expense"]) == -Decimal(body["totals"]["expense"])
    assert Decimal(body["totals"]["expense"]) == Decimal("29175.4684")
    assert Decimal(body["totals"]["income"]) == Decimal("52000")
    assert Decimal(body["totals"]["net"]) == Decimal("52000") - Decimal("29175.4684")
    assert body["totals"]["count"] == 13
    # Each (root, currency) converts on its own, so Σ rows drifts from the currency-only total by rounding.
    row_sum = sum(Decimal(row["amount"]) for row in body["rows"])
    assert row_sum == Decimal("29175.4685")
    assert abs(row_sum - Decimal(body["totals"]["expense"])) < Decimal("0.001")
    assert summary["missing_rates"] == ["JPY"] and body["missing_rates"] == ["JPY"]
    assert (body["currency"], body["date_from"], body["date_to"]) == ("TWD", "2026-10-01", "2026-10-31")
    assert body["compare"] is None and body["overlapping"] is False
    rows = _rows(body)
    assert [row["key"] for row in body["rows"]] == [str(ledger.transport), str(ledger.food), str(ledger.shopping), None]
    assert (rows[str(ledger.food)]["label"], rows[str(ledger.food)]["color"]) == ("餐飲", "#ff8800")
    assert (rows[None]["label"], rows[None]["color"], Decimal(rows[None]["amount"])) == ("", None, Decimal("15"))
    assert rows[str(ledger.food)]["splits"] is None and Decimal(rows[str(ledger.food)]["median"]) == 450


def test_expense_rows_exclude_income_only_categories(client, ledger):
    expense = _breakdown(client, **OCTOBER, group_by="category")
    income = _breakdown(client, **OCTOBER, group_by="category", metric="income")

    assert str(ledger.salary) not in _rows(expense)
    assert Decimal(expense["totals"]["income"]) == Decimal("52000")
    assert [(row["key"], row["label"], Decimal(row["amount"]), row["count"]) for row in income["rows"]] == [
        (str(ledger.salary), "薪資", Decimal("52000"), 1),
    ]
    assert Decimal(income["rows"][0]["refund"]) == 0


def test_group_by_week_keys_are_mondays(client, ledger):
    body = _breakdown(client, date_from="2026-10-05", date_to="2026-10-18", group_by="week")

    assert [row["key"] for row in body["rows"]] == ["2026-10-05", "2026-10-12"]
    assert [row["label"] for row in body["rows"]] == ["2026-10-05", "2026-10-12"]
    # 120 + 95 + 600 in the first week, 1590 + 130 + 450 in the second
    assert [Decimal(row["amount"]) for row in body["rows"]] == [Decimal("815"), Decimal("2170")]


def test_refund_nets_its_category(client, ledger):
    body = _breakdown(client, **OCTOBER, group_by="category")
    shopping = _rows(body)[str(ledger.shopping)]

    assert Decimal(shopping["amount"]) == Decimal("1100.0000")
    assert Decimal(shopping["refund"]) == Decimal("490.0000")
    assert shopping["count"] == 2
    assert Decimal(body["totals"]["refund"]) == Decimal("490.0000")


def test_group_by_merchant_null_row(client, ledger):
    body = _breakdown(client, **OCTOBER, group_by="merchant")
    rows = _rows(body)

    assert (Decimal(rows[None]["amount"]), rows[None]["count"], rows[None]["label"]) == (Decimal("690"), 4, "")
    assert [key for key in rows if key is None] == [None]
    assert (rows["無印良品"]["label"], Decimal(rows["無印良品"]["amount"])) == ("無印良品", Decimal("1100"))


def test_split_by_subcategory_sums_to_row(client, ledger):
    body = _breakdown(client, date_from="2026-09-01", date_to="2026-10-31", group_by="month",
                      split_by="subcategory", category_id=ledger.food)

    assert [row["key"] for row in body["rows"]] == ["2026-09", "2026-10"]
    for row in body["rows"]:
        assert {split["label"] for split in row["splits"]} == {"晚餐", "午餐"}
        assert sum(Decimal(split["amount"]) for split in row["splits"]) == Decimal(row["amount"])
        assert sum(split["count"] for split in row["splits"]) == row["count"]
    october = {split["key"]: split for split in body["rows"][1]["splits"]}
    assert Decimal(october[str(ledger.dinner)]["amount"]) == Decimal("3910")
    assert Decimal(october[str(ledger.lunch)]["amount"]) == Decimal("345") + Decimal("1421.4057")
    assert [split["key"] for split in body["rows"][1]["splits"]] == [str(ledger.dinner), str(ledger.lunch)]


def test_compare_last_year(client, ledger):
    body = _breakdown(client, **OCTOBER, group_by="category", compare="last_year")

    assert (body["compare"]["date_from"], body["compare"]["date_to"]) == ("2025-10-01", "2025-10-31")
    totals = body["compare"]["totals"]
    assert Decimal(totals["expense"]) == Decimal("670")
    assert Decimal(totals["income"]) == Decimal("50000")
    assert Decimal(totals["net"]) == Decimal("49330")
    assert (totals["count"], Decimal(totals["refund"])) == (3, 0)
    rows = _rows(body)
    assert Decimal(rows[str(ledger.food)]["compare_amount"]) == Decimal("670")
    assert rows[str(ledger.transport)]["compare_amount"] is None
    assert rows[str(ledger.shopping)]["compare_amount"] is None
    assert rows[None]["compare_amount"] is None


def test_compare_previous_whole_month(client, ledger):
    body = _breakdown(client, **OCTOBER, group_by="category", compare="previous")

    assert (body["compare"]["date_from"], body["compare"]["date_to"]) == ("2026-09-01", "2026-09-30")
    # 380 + 110 + 100 − 300 + 3000
    assert Decimal(body["compare"]["totals"]["expense"]) == Decimal("3290")
    assert Decimal(_rows(body)[str(ledger.transport)]["compare_amount"]) == Decimal("-200")


def test_invalid_split_422(client, ledger):
    same = client.get("/reports/breakdown", params={**OCTOBER, "group_by": "tag", "split_by": "tag"})
    reversed_dates = client.get("/reports/breakdown", params={
        "date_from": "2026-10-31", "date_to": "2026-10-01", "group_by": "category"})
    unknown = client.get("/reports/breakdown", params={**OCTOBER, "group_by": "colour"})

    assert (same.status_code, reversed_dates.status_code, unknown.status_code) == (422, 422, 422)


def test_excluded_project_left_out_unless_flagged(client, ledger):
    before = Decimal(_breakdown(client, **OCTOBER, group_by="category")["totals"]["expense"])
    response = client.put(f"/projects/{ledger.trip}", json={"name": "東京旅行", "exclude_from_reports": True})
    assert response.status_code == 200, response.text

    excluded = _breakdown(client, **OCTOBER, group_by="category")
    flagged = _breakdown(client, **OCTOBER, group_by="category", include_excluded_projects="true")
    only_trip = _breakdown(client, **OCTOBER, group_by="project", project_id=ledger.trip,
                           include_excluded_projects="true")

    assert before - Decimal(excluded["totals"]["expense"]) == Decimal("24860")
    assert Decimal(flagged["totals"]["expense"]) == before
    assert [(row["key"], row["label"], row["color"], Decimal(row["amount"])) for row in only_trip["rows"]] == [
        (str(ledger.trip), "東京旅行", None, Decimal("24860")),
    ]
    assert Decimal(only_trip["totals"]["expense"]) == Decimal("24860")


def test_weekday_filter(client, ledger):
    body = _breakdown(client, **OCTOBER, group_by="day", weekday=[6, 7])
    by_weekday = _breakdown(client, **OCTOBER, group_by="weekday", weekday=[6, 7])

    assert [row["key"] for row in body["rows"]] == ["2026-10-03", "2026-10-04", "2026-10-10", "2026-10-17"]
    assert all(date.fromisoformat(row["key"]).isoweekday() in (6, 7) for row in body["rows"])
    # 2860 + 22000 + 600 + 450 (the JPY row on 10-04 has no rate)
    assert Decimal(body["totals"]["expense"]) == Decimal("25910")
    assert [(row["key"], row["label"], Decimal(row["amount"])) for row in by_weekday["rows"]] == [
        ("6", "6", Decimal("3910")), ("7", "7", Decimal("22000")),
    ]


def _one_off_merchants(db_session, ledger, prefix: str, count: int, amounts) -> None:
    wallet = db_session.get(Account, ledger.wallet)
    for i in range(1, count + 1):
        for amount in amounts(i):
            make_entry(db_session, wallet, str(-amount), merchant=f"{prefix}{i:03d}", entry_date=date(2026, 10, 16))
    db_session.commit()


def test_fold_over_200_merchants(client, db_session, ledger):
    _one_off_merchants(db_session, ledger, "攤位", 205, lambda i: [i])

    body = _breakdown(client, **OCTOBER, group_by="merchant", merchant="攤位")

    assert len(body["rows"]) == 201
    assert [row["key"] for row in body["rows"][:3]] == ["攤位205", "攤位204", "攤位203"]
    assert body["rows"][199]["key"] == "攤位006"
    other = body["rows"][-1]
    assert (other["key"], other["label"], other["color"], other["count"]) == ("__other__", "__other__", None, 5)
    assert Decimal(other["amount"]) == Decimal("15")  # 1 + 2 + 3 + 4 + 5
    assert Decimal(other["avg"]) == Decimal(other["amount"]) / 5
    assert other["median"] is None and other["first_date"] is None
    assert Decimal(body["totals"]["expense"]) == Decimal(205 * 206 // 2)


def test_metric_count(client, ledger):
    body = _breakdown(client, **OCTOBER, group_by="category", metric="count")

    for row in body["rows"]:
        assert row["amount"] == str(row["count"])
        assert row["avg"] == "1"
        assert Decimal(row["refund"]) == 0
    assert {row["key"]: row["count"] for row in body["rows"]} == {
        str(ledger.food): 7, str(ledger.transport): 2, str(ledger.shopping): 2, str(ledger.salary): 1, None: 1,
    }
    assert [row["key"] for row in body["rows"]][:1] == [str(ledger.food)]
    totals = body["totals"]
    assert (Decimal(totals["expense"]), Decimal(totals["income"]), Decimal(totals["refund"]), totals["count"]) == (
        Decimal("29175.4684"), Decimal("52000"), Decimal("490"), 13,
    )


def test_avg_present_above_50_groups(client, db_session, ledger):
    # 小吃001…小吃051: 10 and i, plus 1 more for every third merchant (a three-entry average that needs rounding)
    _one_off_merchants(db_session, ledger, "小吃", 51, lambda i: [10, i] + ([1] if i % 3 == 0 else []))

    body = _breakdown(client, **OCTOBER, group_by="merchant", merchant="小吃")

    assert len(body["rows"]) == 51
    for row in body["rows"]:
        expected = (Decimal(row["amount"]) / row["count"]).quantize(Decimal("0.0001"))
        assert Decimal(row["avg"]) == expected
        assert row["median"] is None
    assert _rows(body)["小吃001"]["avg"] == "5.5000"
    assert _rows(body)["小吃003"]["avg"] == "4.6667"  # 14 / 3


def test_refund_larger_than_spend_stays_signed(client, ledger):
    body = _breakdown(client, **SEPTEMBER, group_by="category")
    transport = _rows(body)[str(ledger.transport)]

    assert transport["amount"] == "-200.0000"
    assert Decimal(transport["refund"]) == Decimal("300")
    assert transport["count"] == 2
