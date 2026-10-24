"""Pure statement matching (spec §6.2–6.3): gates, representations, conservation, decision, cases.

No DB, no I/O. Contract with callers (the orchestrator follows it):

- Population: ``entries`` are the candidate ledger rows; children (``parent_entry_id`` set) are passed ONLY as
  ``Child`` tuples on their parent, never as standalone ``Entry`` rows. Matching is robust anyway: an ``Entry`` with
  ``parent_entry_id`` never forms a standalone representation. A child is "in reverse" when its parent is.
- Lines are processed in ``id`` order; the revision service assigns ids in print order. Rows claimed by an earlier
  line are unavailable to later lines.
- Known limit (deferred): the decision is greedy per line, so two equal-amount lines against two equal-amount
  entries a day apart can both end ambiguous where a global assignment would pair them.
  Since R6a the exact window is real: distance alone never drops an in-window representation below accept.
"""
from __future__ import annotations

import dataclasses
import itertools

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
    definition_kind: str = "installment"


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
    # False: a mapped installment instance outside the candidate window, passed only so the plan gate can name it
    # (installment_date_drift); never represented, claimed, near/foreign-matched or reported
    claimable: bool = True
    stand_in: bool = False  # R6b.0: a backfilled entry (statement source, applied backfill_entry); owner rows win over it
    # split members: every child as stored (claimed, covered or gated out included) for the printed-whole guard
    # (`_kids`); `children` stays the claimable view and claim paths never read this one
    all_children: tuple[Child, ...] = ()


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
    near_tolerance_pct: Decimal = Decimal("0.05")
    drift_window_days: int = 15  # R6a.5: exact amount, farther than the exact window → a date_drift proposal
    accept: Decimal = Decimal("0.80")
    margin: Decimal = Decimal("0.05")
    ambiguous_floor: Decimal = Decimal("0.50")
    candidate_window_days: int = 10
    deferral_days: int = 2
    bank_only_patterns: tuple[str, ...] = ("年費", "循環利息", "現金回饋", "跨行手續費", "轉帳手續費", "利息")


def population_window_days(rules: Rules) -> int:
    """R6b.0 (shipped with R6a): the orchestrator loads entries this many days around the period so that every
    rule's candidates are present (exact, drift, near, foreign, installment); `candidate_window_days` keeps its
    installment meaning."""
    return max(rules.candidate_window_days, rules.exact_window_days, rules.drift_window_days, rules.near_window_days,
               rules.foreign_window_days)


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
    "purchase": frozenset({"expense", "receivable"}),      # R6a.3: a receivable I paid for someone looks like a purchase
    "installment": frozenset({"expense", "receivable"}),
    "refund": frozenset({"refund"}),
    "payment": frozenset({"transfer_in"}),
    "fee": frozenset({"fee"}),
    "interest": frozenset({"interest"}),
    "reward": frozenset({"reward"}),
}
BANK_GATES = {
    "deposit": frozenset({"income", "transfer_in", "interest", "settlement_in", "payable"}),      # R6a.3: a payable I received
    "withdrawal": frozenset({"expense", "transfer_out", "fee", "settlement_out", "receivable"}),  # R6a.3: a receivable I paid
    "transfer_in": frozenset({"transfer_in"}),
    "transfer_out": frozenset({"transfer_out"}),
    "fee": frozenset({"fee"}),
    "interest": frozenset({"interest"}),
    "refund": frozenset({"refund"}),
    "reward": frozenset({"reward"}),
    "payment": frozenset({"settlement_out", "transfer_out"}),
    "purchase": frozenset({"expense"}),
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
    """R6a.1: any conserved exact-amount representation inside the window scores >= accept (0.80); the date term
    (0.10, linear to the window edge) and the merchant-text term (0.10) only order candidates. A zero window keeps
    same-day candidates only, with the full date term."""
    dist = min(_date_distance(line, e) for e in entries)
    if dist > window:
        return ZERO, ("date_out_of_window",)
    date_term = Decimal("0.10") if window == 0 else (Decimal("0.10") * (Decimal(1) - Decimal(dist) / Decimal(window))).quantize(Q)
    text = _text(line, entries)
    return (Decimal("0.80") + date_term + text).quantize(Q), (f"date_distance={dist}", f"text={text}")


def _installment_score(line: Line, entry: Entry, rules: Rules) -> tuple[Decimal, tuple[str, ...]]:
    """A confirmed plan mapping is the identity: inside the candidate window it stands in for date proximity."""
    dist = _date_distance(line, entry)
    if dist > rules.candidate_window_days:
        return ZERO, ("date_out_of_window",)
    text = _text(line, (entry,))
    return (Decimal("0.90") + text).quantize(Q), (f"date_distance={dist}", "confirmed_plan", f"text={text}")


def _standalone(entry: Entry) -> bool:
    return entry.group_id is None and entry.parent_entry_id is None \
        and (entry.instance is None or entry.instance.definition_kind != "installment")


LONE_MEMBER_LINE_KINDS = frozenset({"fee", "interest", "reward", "refund"})


def _lone_member(entry: Entry, line_kind: str, groups: dict[int, Group]) -> bool:
    """2026-10-19: a top-level member of a `split` group stands alone for a fee, interest, reward or refund line
    (LONE_MEMBER_LINE_KINDS: lines a split purchase never represents) — the fee member of a purchase abroad the
    statement prints as its own 國外交易服務費 line. Never for a purchase, withdrawal, deposit, payment or transfer
    line, never a member of another group kind (reward_claim, installment)."""
    if line_kind not in LONE_MEMBER_LINE_KINDS or entry.group_id is None or entry.parent_entry_id is not None \
            or entry.instance is not None:
        return False
    group = groups.get(entry.group_id)
    return group is not None and group.kind == "split"


def printed_alone(kind: str, lines: list[Line], entries: list[Entry], groups: dict[int, Group], rules: Rules) -> frozenset[int]:
    """The split members this statement prints as their own line: a lone-member candidate (`_lone_member`) of a kind
    the line's gate allows whose flow equals that line's, inside the exact window of it (what an exact claim by that
    line needs). A group reading may leave such members out (`_group_variants`); any other member always counts, so
    a net reading ({expense, discount} for a discounted purchase) is never lost."""
    out: set[int] = set()
    for line in lines:
        if line.line_kind not in LONE_MEMBER_LINE_KINDS:
            continue
        gate = eligible_kinds(kind, line.line_kind)
        out.update(e.id for e in entries
                   if _lone_member(e, line.line_kind, groups) and _entry_class(e) in gate and _same(e.flow, line.flow)
                   and _date_distance(line, e) <= rules.exact_window_days)
    return frozenset(out)


def _peer_ok(kind: str, line: Line, entry: Entry) -> bool:
    """A card `payment` line takes only a leg whose transfer peer is known not to be a card (every candidate path)."""
    return not (line.line_kind == "payment" and kind == "card" and entry.transfer_peer_is_card is not False)


def _same(a: Decimal, b: Decimal) -> bool:
    return a.quantize(Q) == b.quantize(Q)


def _plan_key(line: Line) -> str:
    return f"{line.merchant_norm}|{line.installment_total}|{abs(line.flow).quantize(Q)}"


def _plan_entries(line: Line, entries: list[Entry], definition: int, claimed: set[int]) -> list[Entry]:
    found = [e for e in entries if e.id not in claimed and e.instance is not None and e.instance.definition_id == definition
             and e.instance.seq == line.installment_seq and _same(e.flow, line.flow)]
    return sorted(found, key=lambda e: (_date_distance(line, e), e.id))


ASIDE_COMBINATIONS_MAX = 8  # printed-alone members of one group enumerated in combinations; more: readings incomplete


def _group_variants(g: Group, by_id: dict[int, Entry], claimed: set[int], aside: frozenset[int]) -> tuple[list[list[Entry]], bool]:
    """The member sets a `split` group may represent a line with, and whether the list is complete: the whole group
    (the net reading, as before 2026-10-19) and, for the members the statement prints as their own line (`aside`,
    see `printed_alone`), the group without any combination of them — one printed fee line excuses one fee member,
    two excuse two. A group with a member outside the population or an installment member is never admissible; a
    reading with a claimed member is dropped (a fee member its fee line claimed first leaves the reduced reading
    alone). A reading is dropped when a strict subset of it sums to the same amount (the members left out sum to
    zero — a fee and a reward printed alone go to their own lines); readings that are not nested stay and compete
    (two fees −6 and −4 against one fee −10 tie as ambiguous). Above ASIDE_COMBINATIONS_MAX printed-alone members
    only the whole reading is listed and the result is marked incomplete: the caller never auto-claims such a
    line (`match`), since an omitted reading could compete."""
    members = [by_id.get(i) for i in g.member_ids]
    if any(mem is None or mem.instance is not None for mem in members):
        return [], True
    removable = [mem for mem in members if mem.id in aside]
    readings = [members]
    complete = len(removable) <= ASIDE_COMBINATIONS_MAX
    if removable and complete:
        for size in range(1, len(removable) + 1):
            for combo in itertools.combinations(removable, size):
                dropped = {mem.id for mem in combo}
                rest = [mem for mem in members if mem.id not in dropped]
                if rest:
                    readings.append(rest)
    readings = [r for r in readings if not any(mem.id in claimed for mem in r)]

    def total(reading: list[Entry]) -> Decimal:
        return sum((mem.flow for mem in reading), ZERO).quantize(Q)

    kept = [r for r in readings
            if not any(len(k) < len(r) and {mem.id for mem in k} < {mem.id for mem in r} and total(k) == total(r)
                       for k in readings)]
    return kept, complete


def incomplete_groups(kind: str, line: Line, entries: list[Entry], groups: dict[int, Group], claimed: set[int],
                      aside: frozenset[int]) -> list[int]:
    """The split groups whose readings for this line are incomplete (`_group_variants`): such a line is never
    auto-claimed, an omitted reading could compete with whatever conserves it."""
    if "expense" not in eligible_kinds(kind, line.line_kind):
        return []
    by_id = {e.id: e for e in entries}
    return sorted(g.id for g in groups.values() if g.kind == "split" and not _group_variants(g, by_id, claimed, aside)[1])


def _kids(e: Entry) -> tuple[Child, ...]:
    """The guard's view of a member's children: every child as stored (`all_children`), else the claimable view."""
    return e.all_children or e.children


def _split_member(e: Entry, groups: dict[int, Group]) -> bool:
    """A top-level entry of a `split` group under `_standalone`'s instance rule (no installment instance)."""
    if e.group_id is None or e.parent_entry_id is not None \
            or (e.instance is not None and e.instance.definition_kind == "installment"):
        return False
    group = groups.get(e.group_id)
    return group is not None and group.kind == "split"


def printed_alone_children(kind: str, lines_all: list[Line], entries: list[Entry], groups: dict[int, Group]) -> frozenset[int]:
    """The children of split members this statement prints as their own line: a child (`_kids`) of a kind the line's
    gate admits whose flow equals a line outside `purchase`/`payment` (the child path of `_representations`), over
    every current line whatever its coverage. No date window: the generous direction only makes more groups whole."""
    members = [e for e in entries if _split_member(e, groups)]
    out: set[int] = set()
    for line in lines_all:
        if line.line_kind in ("purchase", "payment"):
            continue
        gate = eligible_kinds(kind, line.line_kind)
        out.update(c.id for e in members for c in _kids(e) if c.kind in gate and _same(c.flow, line.flow))
    return frozenset(out)


@dataclass(frozen=True)
class Reading:
    flow: Decimal
    foreign_amount: Decimal | None
    foreign_currency: str | None
    members: tuple[Entry, ...]


def group_readings(g: Group, by_id: dict[int, Entry], aside: frozenset[int],
                   aside_children: frozenset[int]) -> tuple[list[Reading], bool]:
    """Every total `_representations` could read the split group `g` as on a fresh statement (claimed = ∅): for each
    member reading of `_group_variants` the net total, the total with the claimable child view (`children`: what the
    claim side attaches — a child covered elsewhere is absent from it) and, when its members have children (`_kids`),
    the total with all of them minus each combination of the printed-alone ones (`aside_children`, at most
    ASIDE_COMBINATIONS_MAX; above it only "all children" and the result is incomplete). A reading's foreign total is the members' summed
    `original_amount` when every member carries one in one shared currency. A member missing from `by_id` →
    `([], False)`; the caller treats an incomplete or empty result as printed whole (fail-closed)."""
    if any(i not in by_id for i in g.member_ids):
        return [], False
    variants, complete = _group_variants(g, by_id, set(), aside)
    out: list[Reading] = []
    seen: set[tuple] = set()

    def add(flow: Decimal, members: tuple[Entry, ...]) -> None:
        currencies = {mem.original_currency for mem in members}
        foreign = None
        currency = None
        if all(mem.original_amount is not None for mem in members) and len(currencies) == 1 and None not in currencies:
            foreign = sum((mem.original_amount for mem in members), ZERO).quantize(Q)
            currency = next(iter(currencies))
        key = (flow, foreign, tuple(mem.id for mem in members))
        if key not in seen:
            seen.add(key)
            out.append(Reading(flow, foreign, currency, members))

    for variant in variants:
        members = tuple(variant)
        if not members:
            continue
        net = sum((mem.flow for mem in members), ZERO).quantize(Q)
        add(net, members)
        claimable_kids = [c for mem in members for c in mem.children]  # the view the claim side attaches
        if claimable_kids:
            add((net + sum((c.flow for c in claimable_kids), ZERO)).quantize(Q), members)
        kids = [c for mem in members for c in _kids(mem)]
        if not kids:
            continue
        removable = [c for c in kids if c.id in aside_children]
        if len(removable) > ASIDE_COMBINATIONS_MAX:
            complete = False
            sizes: range = range(0, 1)
        else:
            sizes = range(0, len(removable) + 1)
        for size in sizes:
            for combo in itertools.combinations(removable, size):
                dropped = {c.id for c in combo}
                add((net + sum((c.flow for c in kids if c.id not in dropped), ZERO)).quantize(Q), members)
    return out, complete


def _prints_reading(line: Line, reading: Reading, rules: Rules) -> bool:
    """Spec §3 (a)/(b): the line prints the reading's total — (a) same sign within the near tolerance (taken of the
    total) and the drift window of the nearest member, or (b) the foreign total by magnitude, same currency, within
    the foreign window. The line's kind, gate and coverage are never consulted."""
    dist = min(_date_distance(line, mem) for mem in reading.members)
    tolerance = max(rules.near_tolerance_abs, rules.near_tolerance_pct * abs(reading.flow))
    if line.flow * reading.flow > 0 and abs(line.flow - reading.flow) <= tolerance and dist <= rules.drift_window_days:
        return True
    return reading.foreign_amount is not None and line.foreign_amount is not None and line.foreign_amount != 0 \
        and line.foreign_currency == reading.foreign_currency \
        and _same(abs(reading.foreign_amount), abs(line.foreign_amount)) and dist <= rules.foreign_window_days


def printed_whole(lines_all: list[Line], entries: list[Entry], groups: dict[int, Group], rules: Rules,
                  aside: frozenset[int], aside_children: frozenset[int]) -> frozenset[int]:
    """The split groups this statement prints whole: a reading (`group_readings` over the structural population) a
    current line prints (`_prints_reading`), or readings incomplete or empty (fail-closed: its members never go
    apart)."""
    by_id = {e.id: e for e in entries}
    out: set[int] = set()
    for g in groups.values():
        if g.kind != "split":
            continue
        readings, complete = group_readings(g, by_id, aside, aside_children)
        if not complete or not readings or any(_prints_reading(line, r, rules) for r in readings for line in lines_all):
            out.add(g.id)
    return frozenset(out)


def apart_members(entries: list[Entry], groups: dict[int, Group], whole: frozenset[int]) -> frozenset[int]:
    """The claimable split members (`_split_member`) of groups this statement does not print whole."""
    return frozenset(e.id for e in entries if e.claimable and _split_member(e, groups) and e.group_id not in whole)


@dataclass(frozen=True)
class Context:
    aside: frozenset[int]
    aside_children: frozenset[int]
    whole: frozenset[int]
    apart: frozenset[int]
    structural: tuple[Entry, ...]


def statement_context(kind: str, lines_all: list[Line], entries: list[Entry], groups: dict[int, Group],
                      rules: Rules) -> Context:
    """The per-statement split-member decision over every current line and the unfiltered population (claimable ∪
    structural rows): printed-alone members and children, groups printed whole, and the apart members."""
    aside = printed_alone(kind, lines_all, entries, groups, rules)
    aside_children = printed_alone_children(kind, lines_all, entries, groups)
    whole = printed_whole(lines_all, entries, groups, rules, aside, aside_children)
    return Context(aside, aside_children, whole, apart_members(entries, groups, whole), tuple(entries))


def _representations(kind: str, line: Line, entries: list[Entry], groups: dict[int, Group], plan_map: dict[str, int],
                     claimed: set[int], rules: Rules, aside: frozenset[int] = frozenset(),
                     apart: frozenset[int] = frozenset()) -> list[Representation]:
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
        # group members (save a lone member for a non-purchase line and a member of a split group the statement
        # prints apart, `apart`), installment instances and children never stand alone (children reach a line via the
        # child path)
        if e.id in claimed or not (_standalone(e) or _lone_member(e, line.line_kind, groups) or e.id in apart) \
                or _entry_class(e) not in gate:
            continue
        if not _peer_ok(kind, line, e):
            continue
        principal = (Row(e.id, "principal", e.flow),)
        child_rows = tuple(Row(c.id, "child", c.flow) for c in e.children if c.id not in claimed)
        alone, with_children = ("member_alone", "member_children") if e.id in apart else ("standalone", "principal_children")
        variants = [(alone, rule, principal)]
        if child_rows:
            variants.append((with_children, "exact_children", principal + child_rows))
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
            for members in _group_variants(g, by_id, claimed, aside)[0]:  # none: partial, reserved or installment-member group
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
    if kind == "bank" and line.line_kind in ("payment", "withdrawal"):
        by_id = {e.id: e for e in entries}
        for g in groups.values():
            if g.kind != "installment":
                continue
            members = [by_id.get(i) for i in g.member_ids]
            if any(mem is None or mem.id in claimed or not mem.claimable or mem.instance is None for mem in members):
                continue  # partial, reserved or instance-less groups are never admissible
            if len({mem.instance.definition_id for mem in members}) != 1:
                continue
            rows = tuple(Row(mem.id, "member", mem.flow, g.id) for mem in members)
            if rows and conserved(rows, line.flow):
                score, reasons = _score(line, tuple(members), rules.exact_window_days)
                if score > ZERO:
                    out.append(Representation("installment_group", rows, score, "installment_group", reasons))
    reps = [r for r in out if conserved(r.rows, line.flow)]
    by_id = {e.id: e for e in entries}
    if any(not _is_stand_in_rep(r, by_id) for r in reps):  # R6b.0: an owner representation drops every stand-in one
        reps = [r for r in reps if not _is_stand_in_rep(r, by_id)]
    return reps


def _is_stand_in_rep(rep: Representation, by_id: dict[int, Entry]) -> bool:
    """A representation made only of stand-in rows (a child row counts as its parent's)."""
    owners = {by_id[r.entry_id] for r in rep.rows if r.entry_id in by_id}
    parents = [e for e in by_id.values() if any(r.entry_id in {c.id for c in e.children} for r in rep.rows)]
    return bool(owners | set(parents)) and all(e.stand_in for e in owners | set(parents))


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


def _foreign_groups(kind: str, line: Line, entries: list[Entry], groups: dict[int, Group], claimed: set[int],
                    rules: Rules, aside: frozenset[int]) -> list[tuple[int, Group, list[Entry]]]:
    """Admissible `split` groups whose members all carry the line's foreign currency and whose original amounts sum
    to the printed foreign amount, within the foreign window (2026-10-19: a purchase abroad the owner split into an
    expense and a receivable never foreign-matched, so the policy backfilled the line beside the owner's group).
    Admissibility and the member sets as in `_representations` (`_group_variants`): a fee member printed as its own
    line is left to that line; a group with a member of another kind the statement does not print alone is counted
    whole and fails closed (its original amounts do not sum to the purchase)."""
    gate = eligible_kinds(kind, line.line_kind)
    if "expense" not in gate:
        return []
    by_id = {e.id: e for e in entries}
    out: list[tuple[int, Group, list[Entry]]] = []
    for g in groups.values():
        if g.kind != "split":
            continue
        for members in _group_variants(g, by_id, claimed, aside)[0]:
            if any(not _peer_ok(kind, line, mem) or _entry_class(mem) not in gate for mem in members):
                continue
            if any(mem.original_amount is None or mem.original_amount == 0 or mem.original_currency != line.foreign_currency
                   for mem in members):
                continue
            if not _same(abs(sum((mem.original_amount for mem in members), ZERO)), abs(line.foreign_amount)):
                continue
            dist = min(_date_distance(line, mem) for mem in members)
            if dist <= rules.foreign_window_days:
                out.append((dist, g, members))
                break  # one reading per group
    return sorted(out, key=lambda t: (t[0], t[1].id))


def _foreign_case(kind: str, line: Line, entries: list[Entry], groups: dict[int, Group], claimed: set[int], fee_expected,
                  rules: Rules, currency: str, aside: frozenset[int] = frozenset(),
                  apart: frozenset[int] = frozenset()) -> Case | None:
    """The foreign amounts are compared by magnitude (2026-10-19): a line's `foreign_amount` is stored as the parser
    printed it (positive on most debit lines, negative on some refunds) while an entry's `original_amount` carries
    the entry's sign, so a signed comparison never held for a debit line printed unsigned (the production ledger
    had no foreign case at all); the direction is the gate's and the flows' business. A zero foreign amount (a
    line or an entry) is never a foreign candidate."""
    if line.foreign_amount is None or line.foreign_amount == 0:
        return None
    gate = eligible_kinds(kind, line.line_kind)
    found = sorted((e for e in entries
                    if e.id not in claimed and (_standalone(e) or e.id in apart) and _entry_class(e) in gate and _peer_ok(kind, line, e)
                    and e.original_amount is not None and e.original_amount != 0 and e.original_currency == line.foreign_currency
                    and _same(abs(e.original_amount), abs(line.foreign_amount)) and _date_distance(line, e) <= rules.foreign_window_days),
                   key=lambda e: (_date_distance(line, e), e.id))
    found_groups = _foreign_groups(kind, line, entries, groups, claimed, rules, aside)
    if not found and not found_groups:
        return None
    if len(found) + len(found_groups) > 1:
        return Case(line.id, None, "ambiguous",
                    tuple({"entry_id": e.id, "reason": "foreign", "residual": str((line.flow - e.flow).quantize(Q))} for e in found)
                    + tuple({"group_id": g.id, "reason": "foreign",
                             "residual": str((line.flow - sum((mem.flow for mem in members), ZERO)).quantize(Q))}
                            for _, g, members in found_groups), {})
    if found_groups:
        _, g, members = found_groups[0]
        total = sum((mem.flow for mem in members), ZERO)
        residual = (line.flow - total).quantize(Q)
        fee = fee_expected(dataclasses.replace(members[0], flow=total))  # the account's fee on the whole purchase
        fee_q = None if fee is None else Decimal(fee).quantize(_quantum(currency), rounding=ROUND_HALF_UP)
        matches = fee_q is not None and residual + fee_q == 0
        return Case(line.id, None, "amount_delta", ({"group_id": g.id, "reason": "foreign", "residual": str(residual)},),
                    {"residual": str(residual), "fee_expected": None if fee_q is None else str(fee_q), "fee_matches": matches,
                     "group_id": g.id, "entry_ids": [mem.id for mem in members]})
    e = found[0]
    residual = (line.flow - e.flow).quantize(Q)
    fee = fee_expected(e)
    fee_q = None if fee is None else Decimal(fee).quantize(_quantum(currency), rounding=ROUND_HALF_UP)
    matches = fee_q is not None and residual + fee_q == 0
    return Case(line.id, e.id, "amount_delta", ({"entry_id": e.id, "reason": "foreign"},),
                {"residual": str(residual), "fee_expected": None if fee_q is None else str(fee_q), "fee_matches": matches, "entry_id": e.id})


def _near_case(kind: str, line: Line, entries: list[Entry], claimed: set[int], rules: Rules,
               apart: frozenset[int] = frozenset()) -> Case | None:
    """R6a.4: same amount within max(abs, pct) and the near window. Merchant-token overlap (half of the union) is
    required only for candidates on another day; on the same day amount proximity is enough. Never claims."""
    gate = eligible_kinds(kind, line.line_kind)
    mine = tokens(line.merchant_norm)
    tol = max(rules.near_tolerance_abs, (rules.near_tolerance_pct * abs(line.flow)).quantize(Q))
    found: list[tuple[Decimal, int, int, Decimal]] = []
    for e in entries:
        if e.id in claimed or _entry_class(e) not in gate or not (_standalone(e) or e.id in apart) or not _peer_ok(kind, line, e):
            continue
        delta = (line.flow - e.flow).quantize(Q)
        dist = _date_distance(line, e)
        if abs(delta) > tol or dist > rules.near_window_days:
            continue
        if dist >= 1:
            theirs = tokens(normalise(e.merchant or "")) | tokens(normalise(e.name or ""))
            if not mine or not theirs or 2 * len(mine & theirs) < len(mine | theirs):
                continue
        found.append((abs(delta), dist, e.id, delta))
    if not found:
        return None
    found.sort()
    _, _, best_id, best_delta = found[0]
    cands = tuple({"entry_id": eid, "reason": "near", "residual": str(delta)} for _, _, eid, delta in found)
    return Case(line.id, best_id, "amount_delta", cands, {"residual": str(best_delta), "fee_matches": False, "entry_id": best_id})


def _drift_case(kind: str, line: Line, entries: list[Entry], claimed: set[int], rules: Rules,
                apart: frozenset[int] = frozenset()) -> Case | None:
    """R6a.5: a standalone or apart-member (`apart`), gated, unclaimed entry with the same amount and sign, farther
    than the exact window but within the drift window → an amount_delta proposal (residual 0) the owner confirms with
    `match`. Never claims."""
    gate = eligible_kinds(kind, line.line_kind)
    found: list[tuple[int, int]] = []
    for e in entries:
        if e.id in claimed or _entry_class(e) not in gate or not (_standalone(e) or e.id in apart) or not _peer_ok(kind, line, e):
            continue
        if not _same(e.flow, line.flow):
            continue
        dist = _date_distance(line, e)
        if rules.exact_window_days < dist <= rules.drift_window_days:
            found.append((dist, e.id))
    if not found:
        return None
    found.sort()
    best_dist, best_id = found[0]
    cands = tuple({"entry_id": eid, "reason": "date_drift", "date_distance": dist} for dist, eid in found)
    return Case(line.id, best_id, "amount_delta", cands,
                {"hint": "date_drift", "date_distance": best_dist, "entry_id": best_id, "residual": "0", "fee_matches": False})


BANK_ONLY_KINDS = frozenset({"fee", "interest", "reward"})
PEER_LINE_KINDS = frozenset({"payment", "transfer_in", "transfer_out"})


def _bank_only(line: Line, rules: Rules) -> dict | None:
    """R6a.6: every fee/interest/reward line is bank-side automatic; the matched pattern (if any) only annotates."""
    if line.line_kind not in BANK_ONLY_KINDS:
        return None
    pattern = next((p for p in rules.bank_only_patterns if p in line.merchant_norm), None)
    return {"hint": "bank_only", "pattern": pattern}


def _claimed_by_earlier_line(kind: str, line: Line, entries: list[Entry], groups: dict[int, Group], plan_map: dict[str, int],
                             claimed: set[int], rules: Rules, aside: frozenset[int], apart: frozenset[int]) -> int | None:
    shadow = _representations(kind, line, entries, groups, plan_map, set(), rules, aside, apart)
    if len(shadow) == 1 and any(row.entry_id in claimed for row in shadow[0].rows):
        return shadow[0].rows[0].entry_id
    return None


def _designated_by_cases(cases: list[Case]) -> set[int]:
    """The one entry each `amount_delta` case (near, foreign, drift) pairs with its line (`entry_id`, also in
    `context.entry_id`), or the members of the split group a foreign case pairs (`context.entry_ids`): taken as
    printed here, so never deferred to the next statement. Ambiguous alternatives and
    secondary candidates are not designated (at most one of them is the line) and defer as usual."""
    out: set[int] = set()
    for case in cases:
        if case.kind != "amount_delta":
            continue
        for value in (case.entry_id, (case.context or {}).get("entry_id")):
            if value is not None:
                out.add(value)
        out.update(int(v) for v in (case.context or {}).get("entry_ids") or ())  # a foreign group's members
    return out


def match(statement_kind: str, lines: list[Line], entries: list[Entry], groups: dict[int, Group], plan_map: dict[str, int],
          fee_expected: Callable[[Entry], Decimal | None], rules: Rules, account_currency: str,
          lines_all: list[Line] | None = None) -> MatchResult:
    """`lines_all`: every current line of the statement, covered ones included (default `lines`); `entries` may carry
    structural rows (`claimable=False`), which only the printed-whole guard (`statement_context`) reads."""
    result = MatchResult()
    claimed: set[int] = set()
    ctx = statement_context(statement_kind, lines_all or lines, entries, groups, rules)  # over the unfiltered population
    hint_only = [e for e in entries if not e.claimable]
    entries = [e for e in entries if e.claimable]
    aside, apart = ctx.aside, ctx.apart
    for line in sorted(lines, key=lambda ln: ln.id):
        reps = sorted(_representations(statement_kind, line, entries, groups, plan_map, claimed, rules, aside, apart),
                      key=lambda r: r.score, reverse=True)
        # completeness over the structural population with nothing claimed: fee members covered earlier still count
        truncated = incomplete_groups(statement_kind, line, list(ctx.structural), groups, set(), aside) if reps else []
        if truncated:  # a split group with more printed-alone members than are enumerated: the owner decides
            result.cases.append(Case(line.id, None, "ambiguous", _ambiguous(line, reps).candidates,
                                     {"hint": "group_readings_incomplete", "group_ids": truncated}))
            continue
        if reps and reps[0].score >= rules.accept and (len(reps) == 1 or reps[0].score - reps[1].score >= rules.margin):
            best = reps[0]
            result.claims.append(Claim(line.id, best))
            claimed |= {r.entry_id for r in best.rows}
            continue
        if line.line_kind == "installment":  # gated by the plan map only: never a generic, foreign or near match
            result.cases.append(_installment_case(line, reps, entries + hint_only, plan_map, claimed, rules))
            continue
        if len(reps) >= 2 and reps[0].score >= rules.ambiguous_floor:  # R6a.2: ambiguity needs two candidates
            result.cases.append(_ambiguous(line, reps))
            continue
        if not reps:
            taken = _claimed_by_earlier_line(statement_kind, line, entries, groups, plan_map, claimed, rules, aside, apart)
            if taken is not None:
                result.cases.append(Case(line.id, None, "line_unmatched", (), {"hint": "claimed_by_earlier_line", "entry_id": taken}))
                continue
        case = _foreign_case(statement_kind, line, entries, groups, claimed, fee_expected, rules, account_currency, aside, apart) \
            or _near_case(statement_kind, line, entries, claimed, rules, apart) \
            or _drift_case(statement_kind, line, entries, claimed, rules, apart)
        if case is not None:
            result.cases.append(case)
            continue
        bank_only = _bank_only(line, rules)
        if bank_only:
            result.cases.append(Case(line.id, None, "line_unmatched", (), bank_only))
            continue
        if line.line_kind in PEER_LINE_KINDS:  # R6b.3/R6b.4: transfers wait for R6c; the owner matches or dismisses
            result.cases.append(Case(line.id, None, "line_unmatched", (), {"hint": "needs_peer"}))
            continue
        result.cases.append(Case(line.id, None, "line_unmatched", (), {}))
    unmatched: set[int] = set()
    designated = _designated_by_cases(result.cases)
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
            # a late entry is the next statement's, unless an amount_delta of this statement designates it
            if e.posted_date > rules.period_end - timedelta(days=rules.deferral_days) and e.id not in designated:
                result.explained[e.id] = "deferred_next_period"
            continue
        for c in e.children:
            if c.id not in claimed:
                unmatched.add(c.id)
                result.explained[c.id] = "uncovered_child"
    result.unmatched_entry_ids = sorted(unmatched)
    return result
