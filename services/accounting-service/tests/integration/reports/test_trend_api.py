"""GET /reports/trend: bucket enumeration with empty buckets, series keys, averages, span caps."""

from decimal import Decimal


def _trend(client, **params) -> dict:
    response = client.get("/reports/trend", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_twelve_months_of_food(client, ledger):
    key = str(ledger.food)
    body = _trend(client, date_from="2025-11-01", date_to="2026-10-31", granularity="month", series=[ledger.food])

    assert (body["currency"], body["granularity"]) == ("TWD", "month")
    buckets = body["buckets"]
    assert len(buckets) == 12
    assert buckets[0]["start"] == "2025-11-01" and buckets[-1]["start"] == "2026-10-01"
    values = {bucket["start"]: Decimal(bucket["values"][key]) for bucket in buckets}
    assert all(set(bucket["values"]) == {key} for bucket in buckets)
    assert values["2026-10-01"] == Decimal("5676.4057")
    assert values["2026-09-01"] == Decimal("490")
    assert values["2026-08-01"] == 0 and values["2025-11-01"] == 0  # empty months are present
    assert sum(1 for v in values.values() if v == 0) == 10
    assert Decimal(body["average"][key]) == (Decimal("6166.4057") / 12).quantize(Decimal("0.0001"))


def test_week_buckets_start_monday(client, ledger):
    body = _trend(client, date_from="2026-10-05", date_to="2026-10-18", granularity="week")
    assert [bucket["start"] for bucket in body["buckets"]] == ["2026-10-05", "2026-10-12"]
    # week of 10-05: 120 + 95 + 600 (10-05 salary is income); week of 10-12: 1590 + 130 + 450 - 0 (refund is 10-20)
    first, second = (Decimal(bucket["values"]["all"]) for bucket in body["buckets"])
    assert first == Decimal("815") and second == Decimal("2170")


def test_week_start_before_date_from(client, ledger):
    body = _trend(client, date_from="2026-10-07", date_to="2026-10-18", granularity="week")
    assert [bucket["start"] for bucket in body["buckets"]] == ["2026-10-05", "2026-10-12"]
    assert Decimal(body["buckets"][0]["values"]["all"]) == Decimal("95") + Decimal("600")  # 10-06 is excluded


def test_all_series_key(client, ledger):
    body = _trend(client, date_from="2026-10-01", date_to="2026-10-31", granularity="month")
    assert [bucket["values"].keys() for bucket in body["buckets"]] == [{"all"}]
    assert Decimal(body["buckets"][0]["values"]["all"]) == Decimal("29175.4684")
    assert Decimal(body["average"]["all"]) == Decimal(body["buckets"][0]["values"]["all"])
    assert set(body["average"]) == {"all"}


def test_day_span_cap_422(client, ledger):
    response = client.get("/reports/trend", params={"date_from": "2025-01-01", "date_to": "2026-02-06",
                                                    "granularity": "day"})
    assert response.status_code == 422, response.text
    assert response.json()["message"].startswith("span exceeds"), response.text
    ok = client.get("/reports/trend", params={"date_from": "2025-01-01", "date_to": "2026-02-05",
                                              "granularity": "day"})
    assert ok.status_code == 200 and len(ok.json()["buckets"]) == 401


def test_series_must_be_top_level(client, ledger):
    response = client.get("/reports/trend", params={"date_from": "2026-10-01", "date_to": "2026-10-31",
                                                    "granularity": "month", "series": [ledger.dinner]})
    assert response.status_code == 422
    assert response.json()["message"] == "series must be top-level categories"
