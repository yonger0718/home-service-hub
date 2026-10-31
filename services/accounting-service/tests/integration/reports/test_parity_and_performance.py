"""Timeline/report parity on the shared ledger, and the 12-month breakdown latency on 20,000 entries."""

import statistics
import time
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import insert

from app.models import LedgerEntry, Preference

OCTOBER = {"date_from": "2026-10-01", "date_to": "2026-10-31"}
OCTOBER_DAYS = 31


def test_daily_summary_sum_equals_breakdown_by_day(client, ledger):
    daily = client.get("/entries/summary/daily", params={"month": "2026-10"})
    assert daily.status_code == 200, daily.text
    by_day = client.get("/reports/breakdown", params={**OCTOBER, "group_by": "day"})
    assert by_day.status_code == 200, by_day.text
    daily, by_day = daily.json(), by_day.json()

    # The daily summary converts per (day, currency) and keeps month_summary's negative sign; the breakdown totals
    # convert once per currency over the range, so the two may drift by at most one 4-dp rounding per day.
    daily_expense = -sum(Decimal(day["expense"]) for day in daily["days"])
    report_expense = Decimal(by_day["totals"]["expense"])
    assert abs(daily_expense - report_expense) <= Decimal("0.0005") * OCTOBER_DAYS
    assert report_expense == Decimal("29175.4684")
    assert daily["missing_rates"] == by_day["missing_rates"] == ["JPY"]
    # Day by day the two agree exactly: both convert per (day, currency); income-only days have no expense row.
    daily_rows = {day["date"]: -Decimal(day["expense"]) for day in daily["days"] if Decimal(day["expense"])}
    assert {row["key"]: Decimal(row["amount"]) for row in by_day["rows"]} == daily_rows


ENTRIES = 20_000
MERCHANTS = [f"商家{n:02d}" for n in range(30)]
YEAR_START, YEAR_END = date(2025, 11, 1), date(2026, 10, 31)


def _bulk_rows(account_id: int, categories: list[int], income_category: int) -> list[dict]:
    span = (YEAR_END - YEAR_START).days + 1
    rows = []
    for n in range(ENTRIES):
        day = YEAR_START + timedelta(days=(n * 7919) % span)
        if n % 50 == 0:
            kind, amount, category = "income", Decimal("30000"), income_category
        elif n % 40 == 1:
            kind, amount, category = "refund", Decimal(50 + n % 300), categories[n % len(categories)]
        else:
            kind, amount, category = "expense", -Decimal(35 + (n * 37) % 2400), categories[n % len(categories)]
        rows.append({
            "account_id": account_id, "kind": kind, "amount": amount, "currency": "TWD", "entry_date": day,
            "category_id": category, "merchant": MERCHANTS[n % len(MERCHANTS)] if n % 9 else None,
            "tags": ["旅行"] if n % 17 == 0 else [], "source": "manual",
        })
    return rows


def test_breakdown_12_months_under_300ms_on_20k_entries(client, db_session):
    db_session.add(Preference(main_currency="TWD"))
    db_session.commit()

    def post(path: str, body: dict) -> int:
        response = client.post(path, json=body)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    account = post("/accounts", {"name": "錢包", "currency": "TWD"})
    categories = [post("/categories", {"name": f"類別{n}", "kind": "expense"}) for n in range(8)]
    income = post("/categories", {"name": "薪資", "kind": "income"})
    db_session.execute(insert(LedgerEntry), _bulk_rows(account, categories, income))
    db_session.commit()

    params = {"date_from": YEAR_START.isoformat(), "date_to": YEAR_END.isoformat(), "group_by": "category"}
    warm = time.perf_counter()
    response = client.get("/reports/breakdown", params=params)
    warm_up = time.perf_counter() - warm
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totals"]["count"] == ENTRIES
    assert len(body["rows"]) == 8

    timings = []
    for _ in range(9):
        start = time.perf_counter()
        response = client.get("/reports/breakdown", params=params)
        timings.append(time.perf_counter() - start)
        assert response.status_code == 200, response.text
    median = statistics.median(timings)
    print(f"breakdown 20k: warm-up {warm_up:.4f}s, timings {[round(t, 4) for t in timings]}, median {median:.4f}s")
    assert median < 0.3, f"median {median:.4f}s over 9 calls (warm-up {warm_up:.4f}s): {timings}"
