"""Pair MOZE 轉出/轉入 rows into transfers (design D5). Pure function, no database access."""

import uuid
from dataclasses import dataclass
from typing import Sequence

from .moze_csv import MozeRow


@dataclass(frozen=True)
class PairingResult:
    group_by_row: dict[int, uuid.UUID]  # row_no -> transfer_group_id, for every paired leg
    unpaired_rows: tuple[int, ...]  # row_no of every leg left unpaired, in file order
    pass_counts: tuple[int, int, int]  # transfers paired in pass 1, 2 and 3


def _same_slot(a: MozeRow, b: MozeRow) -> bool:
    return a.entry_date == b.entry_date and a.entry_time == b.entry_time


def _opposite(a: MozeRow, b: MozeRow) -> bool:
    return a.currency == b.currency and a.amount == -b.amount


def _cross(a: MozeRow, b: MozeRow) -> bool:
    return a.currency != b.currency


def pair_transfers(rows: Sequence[MozeRow]) -> PairingResult:
    legs = [r for r in rows if r.kind in ("transfer_out", "transfer_in")]
    by_row = {r.row_no: r for r in legs}
    outs = [r for r in legs if r.kind == "transfer_out"]
    ins = [r for r in legs if r.kind == "transfer_in"]
    partner: dict[int, int] = {}

    def free(r: MozeRow) -> bool:
        return r.row_no not in partner

    def unique_matches(match, excluded: frozenset[int] = frozenset()) -> list[tuple[MozeRow, MozeRow]]:
        def usable(r: MozeRow) -> bool:
            return free(r) and r.row_no not in excluded

        found = []
        for o in outs:
            if not usable(o):
                continue
            candidates = [i for i in ins if usable(i) and _same_slot(o, i) and match(o, i)]
            if len(candidates) != 1:
                continue
            i = candidates[0]
            counterparts = [p for p in outs if usable(p) and _same_slot(p, i) and match(p, i)]
            if len(counterparts) == 1:
                found.append((o, i))
        return found

    counts = [0, 0, 0]

    # Pass 1: the record right after the 轉出 is its exact opposite.
    for o in outs:
        nxt = by_row.get(o.row_no + 1)
        if nxt is not None and nxt.kind == "transfer_in" and free(nxt) and _same_slot(o, nxt) and _opposite(o, nxt):
            partner[o.row_no], partner[nxt.row_no] = nxt.row_no, o.row_no
            counts[0] += 1

    # Pass 2: same currency, exact opposite, unique in both directions.
    # Pass 3: different currency, unique in both directions.
    for o, i in unique_matches(_opposite):
        partner[o.row_no], partner[i.row_no] = i.row_no, o.row_no
        counts[1] += 1

    # A leg that still has a same-currency opposite counterpart was left ambiguous by pass 2;
    # pass 3 must not pair it (or use it as a counterpart) across currencies.
    blocked = frozenset(
        r.row_no
        for r in (*outs, *ins)
        if free(r) and any(free(p) and p.kind != r.kind and _same_slot(r, p) and _opposite(r, p) for p in legs)
    )
    for o, i in unique_matches(_cross, blocked):
        partner[o.row_no], partner[i.row_no] = i.row_no, o.row_no
        counts[2] += 1

    group_by_row: dict[int, uuid.UUID] = {}
    for o in outs:
        if not free(o):
            group = uuid.uuid4()
            group_by_row[o.row_no] = group
            group_by_row[partner[o.row_no]] = group
    unpaired = tuple(r.row_no for r in legs if free(r))
    return PairingResult(group_by_row=group_by_row, unpaired_rows=unpaired, pass_counts=tuple(counts))
