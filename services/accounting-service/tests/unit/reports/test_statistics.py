from datetime import date
from decimal import Decimal

from app.services.report_service import row_statistics


def test_three_entries():
    d1, d3 = date(2026, 10, 1), date(2026, 10, 3)
    stats = row_statistics([(Decimal(100), d1), (Decimal(300), d1), (Decimal(200), d3)])
    assert stats == {
        "median": Decimal("200.0000"), "min": Decimal("100.0000"), "max": Decimal("300.0000"),
        "first_date": d1, "last_date": d3, "avg_interval_days": 1.0,
    }


def test_even_count_median_is_mean_of_middle_pair():
    d = date(2026, 10, 1)
    assert row_statistics([(Decimal(1), d), (Decimal(2), d)])["median"] == Decimal("1.5000")


def test_single_entry_interval_zero():
    d = date(2026, 10, 1)
    assert row_statistics([(Decimal(5), d)])["avg_interval_days"] == 0.0


def test_interval_rounds_to_one_decimal():
    stats = row_statistics([(Decimal(1), date(2026, 10, 1)), (Decimal(1), date(2026, 10, 1)),
                            (Decimal(1), date(2026, 10, 2))])
    assert stats["avg_interval_days"] == 0.5


def test_empty_is_all_none():
    assert row_statistics([]) == {
        "median": None, "min": None, "max": None, "first_date": None, "last_date": None, "avg_interval_days": None,
    }
