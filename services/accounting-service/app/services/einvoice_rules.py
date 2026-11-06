"""Pure e-invoice matching rules (stdlib only)."""

import calendar
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

TargetKey = tuple[str, int]
Decision = Literal["link", "ambiguous", "near", "nothing"]

_TOKEN_RE = re.compile(r"[A-Za-z]+|[0-9]+")
_CJK_RE = re.compile("[㐀-鿿豈-﫿]+")


def period_of(day: date) -> str:
    odd = day.month if day.month % 2 else day.month - 1
    return f"{day.year - 1911:03d}{odd:02d}"


def period_bounds(period: str) -> tuple[date, date]:
    year = int(period[:-2]) + 1911
    odd = int(period[-2:])
    end_month = odd + 1
    return date(year, odd, 1), date(year, end_month, calendar.monthrange(year, end_month)[1])


def is_integral(amount: Decimal) -> bool:
    return amount == amount.to_integral_value()


def near_tolerance(amount: Decimal) -> Decimal:
    return max(Decimal(50), amount * Decimal("0.2"))


def seller_tokens(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    tokens = {t.lower() for t in _TOKEN_RE.findall(text)}
    for run in _CJK_RE.findall(text):
        for size in range(2, len(run) + 1):
            for i in range(len(run) - size + 1):
                tokens.add(run[i : i + size])
    return frozenset(tokens)


def shares_token(seller_name: str, *texts: str | None) -> bool:
    mine = seller_tokens(seller_name)
    return any(mine & seller_tokens(t) for t in texts)


def decide(
    candidates: Mapping[int, Sequence[TargetKey]],
    near: Mapping[int, bool],
    family: Mapping[TargetKey, int | None],
) -> dict[int, tuple[Decision, TargetKey | None]]:
    only = {inv: c[0] for inv, c in candidates.items() if len(c) == 1}
    holders = Counter(only.values())
    group_holders: dict[int, set[int]] = defaultdict(set)
    member_holders: dict[int, set[int]] = defaultdict(set)
    for inv, key in only.items():
        if key[0] == "group":
            group_holders[key[1]].add(inv)
        elif family.get(key) is not None:
            member_holders[family[key]].add(inv)  # type: ignore[index]

    out: dict[int, tuple[Decision, TargetKey | None]] = {}
    for inv, c in candidates.items():
        if len(c) >= 2:
            out[inv] = ("ambiguous", None)
        elif len(c) == 1:
            key = c[0]
            # Only group-vs-own-member is a family contest.
            if key[0] == "group":
                family_contest = member_holders.get(key[1], set())
            else:
                fam = family.get(key)
                family_contest = group_holders.get(fam, set()) if fam is not None else set()
            contested = holders[key] > 1 or bool(family_contest)
            out[inv] = ("ambiguous", None) if contested else ("link", key)
        else:
            out[inv] = ("near", None) if near.get(inv) else ("nothing", None)
    return out
