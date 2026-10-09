"""Pure statement matching (spec §6.2–6.3): gates, representations, conservation, decision, cases.

No DB, no I/O. Contract with callers (the orchestrator follows it):

- Population: ``entries`` are the candidate ledger rows; children (``parent_entry_id`` set) are passed ONLY as
  ``Child`` tuples on their parent, never as standalone ``Entry`` rows. Matching is robust anyway: an ``Entry`` with
  ``parent_entry_id`` never forms a standalone representation. A child is "in reverse" when its parent is.
- Lines are processed in ``id`` order; the revision service assigns ids in print order. Rows claimed by an earlier
  line are unavailable to later lines.
- Known limit (deferred): the decision is greedy per line, so two equal-amount lines against two equal-amount
  entries a day apart can both end ambiguous where a global assignment would pair them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
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


def _text(line: Line, entries: tuple[Entry, ...]) -> Decimal:
    return (Decimal("0.10") * _text_term(line, entries)).quantize(Q)


def _score(line: Line, entries: tuple[Entry, ...], window: int) -> tuple[Decimal, tuple[str, ...]]:
    dist = min(_date_distance(line, e) for e in entries)
    if dist > window:
        return ZERO, ("date_out_of_window",)
    date_term = (Decimal("0.30") * max(ZERO, Decimal(1) - Decimal(dist) / Decimal(4))).quantize(Q)
    text = _text(line, entries)
    return (Decimal("0.60") + date_term + text).quantize(Q), (f"date_distance={dist}", f"text={text}")


def _installment_score(line: Line, entry: Entry, rules: Rules) -> tuple[Decimal, tuple[str, ...]]:
    """A confirmed plan mapping is the identity: inside the candidate window it stands in for date proximity."""
    dist = _date_distance(line, entry)
    if dist > rules.candidate_window_days:
        return ZERO, ("date_out_of_window",)
    text = _text(line, (entry,))
    return (Decimal("0.90") + text).quantize(Q), (f"date_distance={dist}", "confirmed_plan", f"text={text}")


def _standalone(entry: Entry) -> bool:
    return entry.group_id is None and entry.parent_entry_id is None and entry.instance is None


def _same(a: Decimal, b: Decimal) -> bool:
    return a.quantize(Q) == b.quantize(Q)


def _plan_key(line: Line) -> str:
    return f"{line.merchant_norm}|{line.installment_total}|{abs(line.flow).quantize(Q)}"


def _plan_entries(line: Line, entries: list[Entry], definition: int, claimed: set[int]) -> list[Entry]:
    found = [e for e in entries if e.id not in claimed and e.instance is not None and e.instance.definition_id == definition
             and e.instance.seq == line.installment_seq and _same(e.flow, line.flow)]
    return sorted(found, key=lambda e: (_date_distance(line, e), e.id))


def _representations(kind: str, line: Line, entries: list[Entry], groups: dict[int, Group], plan_map: dict[str, int],
                     claimed: set[int], rules: Rules) -> list[Representation]:
    gate = eligible_kinds(kind, line.line_kind)
    out: list[Representation] = []
    if line.line_kind == "installment":
        definition = plan_map.get(_plan_key(line))
        if definition is None:
            return []
        for e in _plan_entries(line, entries, definition, claimed):
            score, reasons = _installment_score(line, e, rules)
            if score > ZERO:
                out.append(Representation("installment", (Row(e.id, "principal", e.flow),), score, "installment", reasons))
        return out
    rule = {"payment": "payment", "refund": "refund", "transfer_in": "transfer", "transfer_out": "transfer"}.get(line.line_kind, "exact")
    for e in entries:
        # group members, installment instances and children never stand alone (children reach a line via the child path)
        if e.id in claimed or not _standalone(e) or _entry_class(e) not in gate:
            continue
        if line.line_kind == "payment" and kind == "card" and e.transfer_peer_is_card is not False:
            continue
        principal = (Row(e.id, "principal", e.flow),)
        child_rows = tuple(Row(c.id, "child", c.flow) for c in e.children if c.id not in claimed)
        variants = [("standalone", rule, principal)]
        if child_rows:
            variants.append(("principal_children", "exact_children", principal + child_rows))
        for rep_kind, rep_rule, rows in variants:
            if conserved(rows, line.flow):
                score, reasons = _score(line, (e,), rules.exact_window_days)
                if score > ZERO:
                    out.append(Representation(rep_kind, rows, score, rep_rule, reasons))
    if line.line_kind not in ("purchase", "payment"):
        # an uncovered fee/discount child can be claimed by a separately printed line (§6.3)
        for e in entries:
            for c in e.children:
                if c.id in claimed or c.kind not in gate or not _same(c.flow, line.flow):
                    continue
                score, reasons = _score(line, (e,), rules.exact_window_days)
                if score > ZERO:
                    out.append(Representation("child", (Row(c.id, "principal", c.flow),), score, "exact", reasons))
    if "expense" in gate:
        by_id = {e.id: e for e in entries}
        for g in groups.values():
            if g.kind != "split":
                continue
            members = [by_id.get(i) for i in g.member_ids]
            if any(mem is None or mem.id in claimed or mem.instance is not None for mem in members):
                continue  # partial, reserved or installment-member groups are never admissible
            member_rows = tuple(Row(mem.id, "member", mem.flow, g.id) for mem in members)
            child_rows = tuple(Row(c.id, "child", c.flow, g.id) for mem in members for c in mem.children if c.id not in claimed)
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


def _ambiguous(line: Line, reps: list[Representation]) -> Case:
    cands = tuple({"entry_id": r.rows[0].entry_id if r.rows[0].group_id is None else None, "group_id": r.rows[0].group_id,
                   "score": str(r.score), "reasons": list(r.reasons)} for r in reps[:5])
    return Case(line.id, None, "ambiguous", cands, {})


def _installment_case(line: Line, reps: list[Representation], entries: list[Entry], plan_map: dict[str, int],
                      claimed: set[int], rules: Rules) -> Case:
    if len(reps) > 1 and reps[0].score >= rules.ambiguous_floor:
        return _ambiguous(line, reps)
    key = _plan_key(line)
    definition = plan_map.get(key)
    if definition is None:
        defs: list[int] = []
        for e in entries:
            if e.id not in claimed and e.instance is not None and e.instance.times == line.installment_total \
                    and _same(e.flow, line.flow) and e.instance.definition_id not in defs:
                defs.append(e.instance.definition_id)
        return Case(line.id, None, "line_unmatched", tuple({"definition_id": d} for d in defs),
                    {"hint": "installment_unmapped", "plan_key": key})
    found = _plan_entries(line, entries, definition, claimed)
    if found:  # confirmed plan, but outside the candidate window or below accept: never auto-claimed
        return Case(line.id, None, "line_unmatched", (),
                    {"hint": "installment_date_drift", "entry_id": found[0].id, "definition_id": definition})
    return Case(line.id, None, "line_unmatched", (), {})


def _foreign_case(kind: str, line: Line, entries: list[Entry], claimed: set[int], fee_expected, rules: Rules,
                  currency: str) -> Case | None:
    if line.foreign_amount is None:
        return None
    gate = eligible_kinds(kind, line.line_kind)
    found = sorted((e for e in entries
                    if e.id not in claimed and _standalone(e) and _entry_class(e) in gate
                    and e.original_amount is not None and e.original_currency == line.foreign_currency
                    and _same(e.original_amount, line.foreign_amount) and _date_distance(line, e) <= rules.foreign_window_days),
                   key=lambda e: (_date_distance(line, e), e.id))
    if not found:
        return None
    if len(found) > 1:
        return Case(line.id, None, "ambiguous",
                    tuple({"entry_id": e.id, "reason": "foreign", "residual": str((line.flow - e.flow).quantize(Q))} for e in found), {})
    e = found[0]
    residual = (line.flow - e.flow).quantize(Q)
    fee = fee_expected(e)
    fee_q = None if fee is None else Decimal(fee).quantize(_quantum(currency), rounding=ROUND_HALF_UP)
    matches = fee_q is not None and residual + fee_q == 0
    return Case(line.id, e.id, "amount_delta", ({"entry_id": e.id, "reason": "foreign"},),
                {"residual": str(residual), "fee_expected": None if fee_q is None else str(fee_q), "fee_matches": matches, "entry_id": e.id})


def _near_case(kind: str, line: Line, entries: list[Entry], claimed: set[int], rules: Rules) -> Case | None:
    gate = eligible_kinds(kind, line.line_kind)
    mine = tokens(line.merchant_norm)
    tol = max(rules.near_tolerance_abs, (rules.near_tolerance_pct * abs(line.flow)).quantize(Q))
    found: list[tuple[Decimal, int, int, Decimal]] = []
    for e in entries:
        if e.id in claimed or _entry_class(e) not in gate or not _standalone(e):
            continue
        theirs = tokens(normalise(e.merchant or "")) | tokens(normalise(e.name or ""))
        if not mine or not theirs or 2 * len(mine & theirs) < len(mine | theirs):
            continue
        delta = (line.flow - e.flow).quantize(Q)
        dist = _date_distance(line, e)
        if abs(delta) > tol or dist > rules.near_window_days:
            continue
        found.append((abs(delta), dist, e.id, delta))
    if not found:
        return None
    found.sort()
    _, _, best_id, best_delta = found[0]
    cands = tuple({"entry_id": eid, "reason": "near", "residual": str(delta)} for _, _, eid, delta in found)
    return Case(line.id, best_id, "amount_delta", cands, {"residual": str(best_delta), "fee_matches": False, "entry_id": best_id})


def _bank_only(line: Line, rules: Rules) -> str | None:
    if line.line_kind not in ("fee", "interest", "reward"):
        return None
    for pattern in rules.bank_only_patterns:
        if pattern in line.merchant_norm:
            return pattern
    return None


def _claimed_by_earlier_line(kind: str, line: Line, entries: list[Entry], groups: dict[int, Group], plan_map: dict[str, int],
                             claimed: set[int], rules: Rules) -> int | None:
    shadow = _representations(kind, line, entries, groups, plan_map, set(), rules)
    if len(shadow) == 1 and any(row.entry_id in claimed for row in shadow[0].rows):
        return shadow[0].rows[0].entry_id
    return None


def match(statement_kind: str, lines: list[Line], entries: list[Entry], groups: dict[int, Group], plan_map: dict[str, int],
          fee_expected: Callable[[Entry], Decimal | None], rules: Rules, account_currency: str) -> MatchResult:
    result = MatchResult()
    claimed: set[int] = set()
    for line in sorted(lines, key=lambda ln: ln.id):
        reps = sorted(_representations(statement_kind, line, entries, groups, plan_map, claimed, rules), key=lambda r: r.score, reverse=True)
        if reps and reps[0].score >= rules.accept and (len(reps) == 1 or reps[0].score - reps[1].score >= rules.margin):
            best = reps[0]
            result.claims.append(Claim(line.id, best))
            claimed |= {r.entry_id for r in best.rows}
            continue
        if line.line_kind == "installment":  # gated by the plan map only: never a generic, foreign or near match
            result.cases.append(_installment_case(line, reps, entries, plan_map, claimed, rules))
            continue
        if reps and reps[0].score >= rules.ambiguous_floor:  # ambiguity stops the line
            result.cases.append(_ambiguous(line, reps))
            continue
        if not reps:
            taken = _claimed_by_earlier_line(statement_kind, line, entries, groups, plan_map, claimed, rules)
            if taken is not None:
                result.cases.append(Case(line.id, None, "line_unmatched", (), {"hint": "claimed_by_earlier_line", "entry_id": taken}))
                continue
        case = _foreign_case(statement_kind, line, entries, claimed, fee_expected, rules, account_currency) \
            or _near_case(statement_kind, line, entries, claimed, rules)
        if case is not None:
            result.cases.append(case)
            continue
        pattern = _bank_only(line, rules)
        if pattern:
            result.cases.append(Case(line.id, None, "line_unmatched", (), {"hint": "bank_only", "pattern": pattern}))
            continue
        result.cases.append(Case(line.id, None, "line_unmatched", (), {}))
    unmatched: set[int] = set()
    for e in entries:
        if not e.in_reverse:
            continue
        if e.parent_entry_id is not None:  # a child passed as a row (outside the contract): reported only under a claimed parent
            if e.id not in claimed and e.parent_entry_id in claimed:
                unmatched.add(e.id)
                result.explained[e.id] = "uncovered_child"
            continue
        if e.id not in claimed:  # an unclaimed parent represents its children
            unmatched.add(e.id)
            if e.posted_date > rules.period_end - timedelta(days=rules.deferral_days):
                result.explained[e.id] = "deferred_next_period"
            continue
        for c in e.children:
            if c.id not in claimed:
                unmatched.add(c.id)
                result.explained[c.id] = "uncovered_child"
    result.unmatched_entry_ids = sorted(unmatched)
    return result
