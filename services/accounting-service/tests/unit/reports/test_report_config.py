"""ReportConfigIn: the versioned `saved_report.config` (reports.model.ts `ReportConfig`)."""

import pytest
from pydantic import ValidationError

from app.schemas.reports import ReportConfigIn, SavedReportIn

PRESETS = ["this_month", "last_month", "last_6_months", "last_12_months", "this_year", "last_year", "all"]


def config(**over) -> dict:
    return {"v": 1, "period": {"preset": "this_month"}, "metric": "expense", "group_by": "category", **over}


@pytest.mark.parametrize("preset", PRESETS)
def test_all_seven_presets_accepted(preset):
    assert ReportConfigIn.model_validate(config(period={"preset": preset})).period.preset == preset


def test_spa_templates_accepted():
    weekend = ReportConfigIn.model_validate(config(filters={"category_id": [2], "weekday": [6, 7]}))
    assert weekend.filters.category_id == [2] and weekend.filters.weekday == [6, 7]
    projects = ReportConfigIn.model_validate(config(filters={"project_id": [3, 4, 1]}))
    assert projects.filters.project_id == [3, 4, 1]


def test_fixed_dates_dump_by_alias():
    dumped = ReportConfigIn.model_validate(config(period={"from": "2026-01-01", "to": "2026-01-31"})).model_dump(
        mode="json", by_alias=True)
    assert dumped["period"] == {"from": "2026-01-01", "to": "2026-01-31"}


@pytest.mark.parametrize("bad", [
    config(split_by="category"),
    config(period={"preset": "next_week"}),
    config(period={"from": "2026-02-01", "to": "2026-01-31"}),
    config(filters={"account_ids": [1]}),
    config(filters={"include_excluded_projects": True}),
    config(filters={"weekday": [8]}),
    config(v=2),
    config(metric="bogus"),
    config(compare="bogus"),
])
def test_invalid_configs_rejected(bad):
    with pytest.raises(ValidationError):
        ReportConfigIn.model_validate(bad)


@pytest.mark.parametrize("filters", [
    {"amount_min": "abc"},
    {"amount_max": "12,5"},
    {"amount_min": "NaN"},
    {"amount_max": "Infinity"},
    {"merchant": "x" * 101},
    {"q": "x" * 101},
    {"tag": ["旅行"] * 101},
    {"account_id": list(range(101))},
    {"category_id": [2**31]},
])
def test_filters_as_strict_as_the_get(filters):
    with pytest.raises(ValidationError):
        ReportConfigIn.model_validate(config(filters=filters))


def test_decimal_bounds_kept_as_sent():
    filters = ReportConfigIn.model_validate(config(filters={"amount_min": "-1500.5", "amount_max": "0",
                                                            "merchant": "x" * 100, "q": "x" * 100})).filters
    assert (filters.amount_min, filters.amount_max) == ("-1500.5", "0")


@pytest.mark.parametrize("sort_order", [2**31, -(2**31) - 1])
def test_sort_order_fits_int4(sort_order):
    body = {"name": "r", "config": config(), "chart": "bar", "pinned": False, "sort_order": sort_order}
    with pytest.raises(ValidationError):
        SavedReportIn.model_validate(body)
    assert SavedReportIn.model_validate({**body, "sort_order": 2**31 - 1}).sort_order == 2**31 - 1
