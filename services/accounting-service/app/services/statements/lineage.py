from collections import Counter
from dataclasses import dataclass

from app.services.statements.merchant import tokens


@dataclass(frozen=True)
class Old:
    line_id: int
    event_id: int
    logical_key: str
    canonical_key: str
    merchant_norm: str
    kind_fields: tuple


@dataclass(frozen=True)
class New:
    index: int
    logical_key: str
    canonical_key: str
    merchant_norm: str
    kind_fields: tuple


@dataclass(frozen=True)
class Pairing:
    old: Old | None
    new: New | None
    equivalence: str


def _equivalence(old: Old, new: New) -> str:
    if old.canonical_key == new.canonical_key:
        return "identical"
    if old.kind_fields == new.kind_fields and tokens(old.merchant_norm) == tokens(new.merchant_norm):
        return "normalised"
    return "changed"


def pair(old: list[Old], new: list[New]) -> list[Pairing]:
    """Pair by logical key in print order; classify by canonical key / token equality (design §5.7)."""
    by_key: dict[str, list[Old]] = {}
    for item in sorted(old, key=lambda o: o.line_id):
        by_key.setdefault(item.logical_key, []).append(item)
    out: list[Pairing] = []
    for item in sorted(new, key=lambda n: n.index):
        bucket = by_key.get(item.logical_key)
        if bucket:
            matched = bucket.pop(0)
            out.append(Pairing(matched, item, _equivalence(matched, item)))
        else:
            out.append(Pairing(None, item, "unpaired"))
    for bucket in by_key.values():
        for leftover in bucket:
            out.append(Pairing(leftover, None, "unpaired"))
    return out


def twin_count_changed(old: list[Old], new: list[New]) -> bool:
    def twins(items, key):
        return {k: c for k, c in Counter(key(i) for i in items).items() if c > 1}

    def strip(lk):
        return lk.rsplit("|", 1)[0]  # (posted_date, flow) without the occurrence index

    return twins(old, lambda o: strip(o.logical_key)) != twins(new, lambda n: strip(n.logical_key))
