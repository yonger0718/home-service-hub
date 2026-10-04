"""Pure date maths of schedule rules (design D30, D41).

Occurrence k is computed from the anchor every time, never from the previous occurrence, so a monthly rule on the
31st clamps to the end of February and returns to the 31st in March. Dates are naive Asia/Taipei dates.
"""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

HORIZON_MONTHS = 13


def days_in_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def add_months(day: date, months: int, day_of_month: int | None = None) -> date:
    """`day` moved by `months` calendar months, on `day_of_month` (default: day's own day), clamped to the month."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    target = day_of_month if day_of_month is not None else day.day
    return date(year, month, min(target, days_in_month(year, month)))


def _months_per_step(unit: str, n: int) -> int:
    return n if unit == "month" else 12 * n


def occurrence(anchor: date, unit: str, n: int, k: int, day_of_month: int | None = None) -> date:
    if k < 0:
        raise ValueError("occurrence index must be ≥ 0")
    if unit == "day":
        return anchor + timedelta(days=k * n)
    if unit == "week":
        return anchor + timedelta(days=7 * k * n)
    if unit in ("month", "year"):
        day = day_of_month if day_of_month is not None else anchor.day
        return add_months(anchor, k * _months_per_step(unit, n), day)
    raise ValueError(f"unknown interval unit {unit!r}")


def _estimate(anchor: date, unit: str, n: int, day: date) -> int:
    """A k whose occurrence is close to `day` (never negative); callers step from it."""
    if unit in ("day", "week"):
        step = n if unit == "day" else 7 * n
        return max((day - anchor).days // step, 0)
    months = (day.year - anchor.year) * 12 + day.month - anchor.month
    return max(months // _months_per_step(unit, n), 0)


def first_index_on_or_after(anchor: date, unit: str, n: int, day_of_month: int | None, on: date) -> int:
    """The smallest k ≥ 0 whose occurrence is on or after `on`."""
    k = _estimate(anchor, unit, n, on)
    while k > 0 and occurrence(anchor, unit, n, k - 1, day_of_month) >= on:
        k -= 1
    while occurrence(anchor, unit, n, k, day_of_month) < on:
        k += 1
    return k


def first_index_after(anchor: date, unit: str, n: int, day_of_month: int | None, after: date) -> int:
    """The smallest k ≥ 0 whose occurrence is strictly after `after`."""
    return first_index_on_or_after(anchor, unit, n, day_of_month, after + timedelta(days=1))


def normalize_anchor(anchor: date, unit: str, n: int, day_of_month: int | None) -> date:
    """The rule's first occurrence on or after `anchor`: the stored anchor_date is occurrence 0 (spec), so a
    day_of_month earlier than the anchor's day (anchor 09-20, day 15) starts on 10-15, never on 09-15."""
    return occurrence(anchor, unit, n, first_index_on_or_after(anchor, unit, n, day_of_month, anchor), day_of_month)


def occurrence_index(anchor: date, unit: str, n: int, day_of_month: int | None, day: date) -> int | None:
    """k when `day` is occurrence k of the rule, else None. Compared with occurrence 0, not the anchor: with a
    day_of_month earlier than the anchor's day, occurrence 0 precedes the anchor."""
    if day < occurrence(anchor, unit, n, 0, day_of_month):
        return None
    k = first_index_on_or_after(anchor, unit, n, day_of_month, day)
    return k if occurrence(anchor, unit, n, k, day_of_month) == day else None


def horizon(today: date) -> date:
    """today + 13 months, same day of month, clamped (spec "Instance generation")."""
    return add_months(today, HORIZON_MONTHS)


def last_period_amount(total: Decimal, per_period: Decimal, times: int) -> Decimal:
    """The last period of an installment: what the other periods leave of the total (MOZE 分期餘額納入 末期)."""
    last = Decimal(total) - Decimal(per_period) * (times - 1)
    if last <= 0:
        raise ValueError("the per-period amount times (times − 1) reaches the total")
    return last


def plain(value: Decimal | str | int) -> str:
    """A decimal as an unsigned-looking plain string without trailing zeros: '8333', '0.5', '10'."""
    normalized = Decimal(str(value)).normalize()
    return format(normalized, "f")
