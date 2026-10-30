"""R7 Phase 1: assign 國泰 settlements to bank settlement lines (pure; no DB).

A settlement line (`matching.is_settlement_line`) is matched to a portfolio settlement by exact amount and a date
window. The assignment is global: a settlement wanted by two lines, or a line with two candidates, is never booked
automatically (`ambiguous`). Calendar expiry fails closed (`CalendarExpired`).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from app.services.business_days import add_business_days, calendar_covers
from app.services.portfolio_client import Settlement
from app.services.statements.matching import Line, Rules, _same, is_settlement_line

SETTLE_DAYS = 2  # T+2 business days


class CalendarExpired(ValueError):
    """The business-day table ends before the range: the trade bound cannot be proven complete."""


def trade_bounds(settle_from: date, settle_to: date, *, extra_holidays: frozenset[date],
                 extra_workdays: frozenset[date]) -> tuple[date, date]:
    """The trade range whose T+2 can land in [settle_from, settle_to]: `trade_from` is the earliest day whose T+2 is
    on or after `settle_from` (walked back until the condition fails), `trade_to` = `settle_to`."""
    if not calendar_covers(settle_to):
        raise CalendarExpired(str(settle_to))

    def settles(t: date) -> date:
        d = add_business_days(t, SETTLE_DAYS, extra_holidays=extra_holidays, extra_workdays=extra_workdays)
        if d is None:
            raise CalendarExpired(str(t))
        return d

    trade_from = settle_from
    while settles(trade_from - timedelta(days=1)) >= settle_from:
        trade_from -= timedelta(days=1)
    return trade_from, settle_to


def with_dates(rows: list[Settlement], *, extra_holidays: frozenset[date],
               extra_workdays: frozenset[date]) -> list[Settlement]:
    """A `legs` row has no settlement date: derive T+2 from the bank calendar (None stays None: `assign` counts it)."""
    return [replace(r, settlement_date=add_business_days(r.trade_date, SETTLE_DAYS, extra_holidays=extra_holidays,
                                                        extra_workdays=extra_workdays))
            if r.date_source == "legs" else r for r in rows]


@dataclass
class Assignment:
    booked: dict[int, Settlement] = field(default_factory=dict)
    ambiguous: dict[int, list[Settlement]] = field(default_factory=dict)
    pending: dict[int, str | None] = field(default_factory=dict)
    unknown_dates: int = 0


def _merge(parts: tuple[Settlement, ...]) -> Settlement | None:
    """Two settlements as one candidate; None unless they agree on trade date, settlement date and date source."""
    first = parts[0]
    if any((p.trade_date, p.settlement_date, p.date_source) != (first.trade_date, first.settlement_date, first.date_source)
           for p in parts):
        return None
    parts = tuple(sorted(parts, key=lambda p: p.fingerprint))
    return replace(first, net_amount=sum((p.net_amount for p in parts), Decimal(0)),
                   legs_net=sum((p.legs_net for p in parts), Decimal(0)),
                   bookable=all(p.bookable for p in parts),
                   reason=next((p.reason for p in parts if p.reason), None),
                   items=tuple(i for p in parts for i in p.items),
                   fingerprint="+".join(p.fingerprint for p in parts))


def assign(lines: list[Line], settlements: list[Settlement], *, reserved_fps: set[str], reserved_ids: set[str],
           contestants: set[int], rules: Rules) -> Assignment:
    """Candidates of a line: same amount, dated, within `settlement_window_days` of the posted date, not reserved
    (fingerprint or any constituent). Sum candidates (two settlements) only under `settlement_sum_matching`.
    Booked: one candidate, no constituent wanted by another line (contestants compete but are never reported), and
    bookable; a sole non-bookable candidate → pending with its reason; no candidate → pending None; else ambiguous."""
    out = Assignment()
    free = [s for s in settlements if s.fingerprint not in reserved_fps and not reserved_ids.intersection(s.source_ids)]
    out.unknown_dates = sum(1 for s in free if s.settlement_date is None)
    pool = [s for s in free if s.settlement_date is not None]
    singles = [(s,) for s in pool]
    sums = [c for c in combinations(pool, 2) if not set(c[0].source_ids) & set(c[1].source_ids)] \
        if rules.settlement_sum_matching else []
    cands: dict[int, list[Settlement]] = {}
    for line in lines:
        if not is_settlement_line(line, rules):
            continue
        found = []
        for parts in singles + sums:
            merged = parts[0] if len(parts) == 1 else _merge(parts)
            if merged is not None and _same(merged.net_amount, line.flow) and all(
                    abs((p.settlement_date - line.posted_date).days) <= rules.settlement_window_days for p in parts):
                found.append(merged)
        cands[line.id] = found
    wanted: dict[str, set[int]] = {}
    for lid, found in cands.items():
        for c in found:
            for sid in c.source_ids:
                wanted.setdefault(sid, set()).add(lid)
    for lid, found in cands.items():
        if lid in contestants:
            continue
        if not found:
            out.pending[lid] = None
            continue
        contested = any(wanted[sid] - {lid} for c in found for sid in c.source_ids)
        if len(found) == 1 and not contested:
            if found[0].bookable:
                out.booked[lid] = found[0]
            else:
                out.pending[lid] = found[0].reason
        else:
            out.ambiguous[lid] = found
    return out
