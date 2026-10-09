from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable

from app.services.statements.merchant import normalise, tokens

Q = Decimal("0.0001")
ZERO = Decimal(0)


@dataclass(frozen=True)
class Child:
    id: int
    kind: str
    flow: Decimal


@dataclass(frozen=True)
class InstanceRef:
    definition_id: int
    seq: int
    times: int | None


@dataclass(frozen=True)
class Line:
    id: int
    event_id: int
    posted_date: date
    txn_date: date | None
    flow: Decimal
    foreign_amount: Decimal | None
    foreign_currency: str | None
    line_kind: str
    merchant_norm: str
    installment_seq: int | None
    installment_total: int | None


@dataclass(frozen=True)
class Entry:
    id: int
    kind: str
    flow: Decimal
    posted_date: date
    entry_date: date
    name: str | None
    merchant: str | None
    original_amount: Decimal | None
    original_currency: str | None
    account_id: int
    group_id: int | None
    parent_entry_id: int | None
    is_settlement: bool
    refunds_entry_id: int | None
    transfer_group_id: str | None
    transfer_peer_is_card: bool | None
    children: tuple[Child, ...]
    instance: InstanceRef | None
    in_reverse: bool = True


@dataclass(frozen=True)
class Group:
    id: int
    kind: str
    member_ids: tuple[int, ...]


@dataclass(frozen=True)
class Rules:
    period_end: date
    exact_window_days: int = 3
    foreign_window_days: int = 5
    near_window_days: int = 5
    near_tolerance_abs: Decimal = Decimal("10")
    near_tolerance_pct: Decimal = Decimal("0.03")
    accept: Decimal = Decimal("0.80")
    margin: Decimal = Decimal("0.15")
    ambiguous_floor: Decimal = Decimal("0.50")
    candidate_window_days: int = 10
    deferral_days: int = 2
    bank_only_patterns: tuple[str, ...] = ("年費", "循環利息", "現金回饋", "跨行手續費", "轉帳手續費", "利息")


@dataclass(frozen=True)
class Row:
    entry_id: int
    role: str
    flow: Decimal
    group_id: int | None = None


@dataclass(frozen=True)
class Representation:
    kind: str
    rows: tuple[Row, ...]
    score: Decimal
    rule: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Claim:
    line_id: int
    representation: Representation


@dataclass(frozen=True)
class Case:
    line_id: int | None
    entry_id: int | None
    kind: str
    candidates: tuple[dict, ...]
    context: dict


@dataclass
class MatchResult:
    claims: list[Claim] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)
    unmatched_entry_ids: list[int] = field(default_factory=list)
    explained: dict[int, str] = field(default_factory=dict)


CARD_GATES = {
    "purchase": frozenset({"expense"}),
    "installment": frozenset({"expense"}),
    "refund": frozenset({"refund"}),
    "payment": frozenset({"transfer_in"}),
    "fee": frozenset({"fee"}),
    "interest": frozenset({"interest"}),
    "reward": frozenset({"reward"}),
}
BANK_GATES = {
    "deposit": frozenset({"income", "transfer_in", "interest", "settlement_in"}),
    "withdrawal": frozenset({"expense", "transfer_out", "fee", "settlement_out"}),
    "transfer_in": frozenset({"transfer_in"}),
    "transfer_out": frozenset({"transfer_out"}),
    "fee": frozenset({"fee"}),
    "interest": frozenset({"interest"}),
    "refund": frozenset({"refund"}),
    "reward": frozenset({"reward"}),
}


def eligible_kinds(statement_kind: str, line_kind: str) -> frozenset[str]:
    table = CARD_GATES if statement_kind == "card" else BANK_GATES
    return table.get(line_kind, frozenset())


def _entry_class(entry: Entry) -> str:
    if entry.is_settlement:
        return "settlement_in" if entry.flow > 0 else "settlement_out"
    return entry.kind


def conserved(rows: tuple[Row, ...], flow: Decimal) -> bool:
    return sum((r.flow for r in rows), ZERO).quantize(Q) == flow.quantize(Q)


def _days(a: date, b: date) -> int:
    return abs((a - b).days)


def _date_distance(line: Line, entry: Entry) -> int:
    d = _days(line.posted_date, entry.posted_date)
    if line.txn_date is not None:
        d = min(d, _days(line.txn_date, entry.entry_date))
    return d


def _text_term(line: Line, entries: tuple[Entry, ...]) -> Decimal:
    mine = tokens(line.merchant_norm)
    theirs: set[str] = set()
    for e in entries:
        theirs |= tokens(normalise(e.merchant or "")) | tokens(normalise(e.name or ""))
    if not mine or not theirs:
        return ZERO
    inter = len(mine & theirs)
    union = len(mine | theirs)
    return (Decimal(inter) / Decimal(union)).quantize(Q)


def _score(line: Line, entries: tuple[Entry, ...], window: int) -> tuple[Decimal, tuple[str, ...]]:
    dist = min(_date_distance(line, e) for e in entries)
    if dist > window:
        return ZERO, ("date_out_of_window",)
    date_term = (Decimal("0.30") * max(ZERO, Decimal(1) - Decimal(dist) / Decimal(4))).quantize(Q)
    text = (Decimal("0.10") * _text_term(line, entries)).quantize(Q)
    return (Decimal("0.60") + date_term + text).quantize(Q), (f"date_distance={dist}", f"text={text}")


def _standalone(entry: Entry) -> bool:
    return entry.group_id is None and entry.parent_entry_id is None and entry.instance is None


def _representations(kind: str, line: Line, entries: list[Entry], groups: dict[int, Group], plan_map: dict[str, int],
                     claimed: set[int], rules: Rules) -> list[Representation]:
    by_id = {e.id: e for e in entries}
    out: list[Representation] = []
    gate = eligible_kinds(kind, line.line_kind)
    if line.line_kind == "installment":
        key = f"{line.merchant_norm}|{line.installment_total}|{abs(line.flow).quantize(Q)}"
        definition = plan_map.get(key)
        if definition is None:
            return []
        for e in entries:
            if e.instance and e.instance.definition_id == definition and e.instance.seq == line.installment_seq \
                    and e.flow.quantize(Q) == line.flow.quantize(Q) and e.id not in claimed:
                score, reasons = _score(line, (e,), rules.exact_window_days)
                out.append(Representation("installment", (Row(e.id, "principal", e.flow),), score, "installment", reasons))
        return out
    for e in entries:
        if e.id in claimed or _entry_class(e) not in gate:
            continue
        if line.line_kind == "payment" and kind == "card" and e.transfer_peer_is_card is not False:
            continue
        if line.line_kind in ("purchase",) and not _standalone(e):
            continue
        if e.flow.quantize(Q) != line.flow.quantize(Q):
            continue
        rule = {"payment": "payment", "refund": "refund", "transfer_in": "transfer", "transfer_out": "transfer"}.get(line.line_kind, "exact")
        score, reasons = _score(line, (e,), rules.exact_window_days)
        if score > ZERO:
            out.append(Representation("standalone", (Row(e.id, "principal", e.flow),), score, rule, reasons))
    if line.line_kind not in ("purchase", "payment"):
        # an uncovered fee/discount child can be claimed by a separately printed line (§6.3)
        for e in entries:
            for c in e.children:
                if c.id in claimed or c.kind not in gate or c.flow.quantize(Q) != line.flow.quantize(Q):
                    continue
                score, reasons = _score(line, (e,), rules.exact_window_days)
                if score > ZERO:
                    out.append(Representation("child", (Row(c.id, "principal", c.flow),), score, "exact", reasons))
    if line.line_kind == "purchase":
        for g in groups.values():
            if g.kind != "split":
                continue
            members = [by_id.get(i) for i in g.member_ids]
            if any(m is None or m.id in claimed or m.instance is not None for m in members):
                continue  # partial, reserved or installment-member groups are never admissible
            member_rows = tuple(Row(m.id, "member", m.flow, g.id) for m in members)
            child_rows = tuple(Row(c.id, "child", c.flow, g.id) for m in members for c in m.children if c.id not in claimed)
            variants = [("group", member_rows)]
            if child_rows:
                variants.append(("group_children", member_rows + child_rows))
            for variant, rows in variants:
                if rows and conserved(rows, line.flow):
                    score, reasons = _score(line, tuple(members), rules.exact_window_days)
                    if score > ZERO:
                        out.append(Representation(variant, rows, score, variant, reasons))
    return [r for r in out if conserved(r.rows, line.flow)]


def _quantum(currency: str) -> Decimal:
    return Decimal("1") if currency in ("TWD", "JPY") else Decimal("0.01")


def _foreign_case(kind: str, line: Line, entries: list[Entry], claimed: set[int], fee_expected, rules: Rules,
                  currency: str) -> Case | None:
    if line.foreign_amount is None:
        return None
    gate = eligible_kinds(kind, line.line_kind)
    for e in entries:
        if e.id in claimed or _entry_class(e) not in gate:
            continue
        if e.original_amount is None or e.original_currency != line.foreign_currency:
            continue
        if e.original_amount.quantize(Q) != line.foreign_amount.quantize(Q) or _date_distance(line, e) > rules.foreign_window_days:
            continue
        residual = (line.flow - e.flow).quantize(Q)
        fee = fee_expected(e)
        matches = fee is not None and abs(residual + fee) < _quantum(currency)
        return Case(line.id, e.id, "amount_delta", ({"entry_id": e.id, "reason": "foreign"},),
                    {"residual": str(residual), "fee_expected": str(fee) if fee is not None else None, "fee_matches": bool(matches), "entry_id": e.id})
    return None


def _near_case(kind: str, line: Line, entries: list[Entry], claimed: set[int], rules: Rules) -> Case | None:
    gate = eligible_kinds(kind, line.line_kind)
    mine = tokens(line.merchant_norm)
    best: tuple[Decimal, Entry] | None = None
    for e in entries:
        if e.id in claimed or _entry_class(e) not in gate or not _standalone(e):
            continue
        theirs = tokens(normalise(e.merchant or "")) | tokens(normalise(e.name or ""))
        if not mine or not theirs or len(mine & theirs) / len(mine | theirs) < 0.5:
            continue
        delta = (line.flow - e.flow).quantize(Q)
        tol = max(rules.near_tolerance_abs, (rules.near_tolerance_pct * abs(line.flow)).quantize(Q))
        if abs(delta) > tol or _date_distance(line, e) > rules.near_window_days:
            continue
        if best is None or abs(delta) < abs((line.flow - best[1].flow)):
            best = (delta, e)
    if best is None:
        return None
    delta, e = best
    return Case(line.id, e.id, "amount_delta", ({"entry_id": e.id, "reason": "near"},), {"residual": str(delta), "fee_matches": False, "entry_id": e.id})


def _bank_only(line: Line, rules: Rules) -> str | None:
    if line.line_kind not in ("fee", "interest", "reward"):
        return None
    for pattern in rules.bank_only_patterns:
        if pattern in line.merchant_norm:
            return pattern
    return None


def match(statement_kind: str, lines: list[Line], entries: list[Entry], groups: dict[int, Group], plan_map: dict[str, int],
          fee_expected: Callable[[Entry], Decimal | None], rules: Rules, account_currency: str) -> MatchResult:
    result = MatchResult()
    claimed: set[int] = set()
    for line in sorted(lines, key=lambda l: l.id):
        reps = sorted(_representations(statement_kind, line, entries, groups, plan_map, claimed, rules), key=lambda r: r.score, reverse=True)
        if reps and reps[0].score >= rules.accept and (len(reps) == 1 or reps[0].score - reps[1].score >= rules.margin):
            best = reps[0]
            result.claims.append(Claim(line.id, best))
            claimed |= {r.entry_id for r in best.rows}
            continue
        if reps and reps[0].score >= rules.ambiguous_floor:
            cands = tuple({"entry_id": r.rows[0].entry_id if r.rows[0].group_id is None else None, "group_id": r.rows[0].group_id,
                           "score": str(r.score), "reasons": list(r.reasons)} for r in reps[:5])
            result.cases.append(Case(line.id, None, "ambiguous", cands, {}))
            continue
        if line.line_kind == "installment":
            key = f"{line.merchant_norm}|{line.installment_total}|{abs(line.flow).quantize(Q)}"
            if key not in plan_map:
                defs = tuple({"definition_id": e.instance.definition_id} for e in entries
                             if e.instance and e.instance.times == line.installment_total and e.flow.quantize(Q) == line.flow.quantize(Q))
                result.cases.append(Case(line.id, None, "line_unmatched", defs, {"hint": "installment_unmapped", "plan_key": key}))
                continue
        case = _foreign_case(statement_kind, line, entries, claimed, fee_expected, rules, account_currency) or _near_case(statement_kind, line, entries, claimed, rules)
        if case is not None:
            result.cases.append(case)
            continue
        pattern = _bank_only(line, rules)
        if pattern:
            result.cases.append(Case(line.id, None, "line_unmatched", (), {"hint": "bank_only", "pattern": pattern}))
            continue
        result.cases.append(Case(line.id, None, "line_unmatched", (), {}))
    for e in entries:
        if e.in_reverse and e.id not in claimed and e.parent_entry_id is None:
            result.unmatched_entry_ids.append(e.id)
            if e.posted_date > rules.period_end - timedelta(days=rules.deferral_days):
                result.explained[e.id] = "deferred_next_period"
    result.unmatched_entry_ids.sort()
    return result
