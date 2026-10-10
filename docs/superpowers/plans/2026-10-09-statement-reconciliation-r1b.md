# Statement reconciliation R1b — matching, coverage, dirty sweep — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Second stacked backend PR of 對帳: deterministic matching of statement lines to ledger rows (eligibility gates, competing representations, monetary conservation), persisted coverage with the global unique claim, the dirty-event sweep that keeps coverage honest after ledger edits, coverage transfer/quarantine across re-parses, cases from matching (live mode), deferral and cross-account hints, bank balance gaps, and the `reconcile`/`sweep` endpoints. No actions, proposals, policy or confirm (R1c).

**Architecture:** A pure matching module (`app/services/statements/matching.py`) takes plain dataclasses and returns claims + cases; `coverage_service.py` persists claims, asserts conservation, releases claims and runs the watermark sweep over `coverage_dirty`; `reconciliation_service.py` loads populations under the §7.4 lock order and orchestrates sweep → match → coverage → cases → deferrals → cross-account → balance gap → counts; `statement_revision_service.py` gains lineage coverage transfer/quarantine and calls reconcile when a revision becomes current. Triggers (R1a) are the only invalidation source in R1b — no service hooks (ruling below).

**Tech Stack:** as R1a (FastAPI, SQLAlchemy 2.0 `Column` models, Postgres, pytest with `client`/`db_session`/`seed`).

**Spec:** `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` v4 (§4.6, §4.7, §5.7, §6, §7.4, §8 reconcile route, §16 C3/C5/C6 acceptance items, §17 notes). Base: `main` 63eb131 (R1a merged).

## Global Constraints

- Lock order (§7.4, D32): `import advisory key (shared) → account_statement row (FOR UPDATE, ascending id) → entry_group rows (ascending) → ledger entries with legs (one ordered FOR UPDATE)`. Reconcile never locks `account` rows; edit paths never lock statements (they emit dirty events through the R1a triggers).
- Monetary conservation (§4.6): for every line with active coverage, `Σ snapshot.flow of its rows == line.flow_amount` exactly (`Decimal`, 4 dp); asserted before every commit that writes coverage.
- One active claim per ledger row (`ux_statement_coverage_active_entry`); a group claim reserves every member it was scored with; children only in the "with children" variant.
- Cases only on `live` statements; historical statements get coverage + summary counts, never cases (§5.8).
- Scoring (§6.3): `0.60` exact amount + `0.30 × max(0, 1 − date_distance/4)` + `0.10 × Jaccard(tokens)`; accept when best `≥ 0.80` and beats every other admissible representation by `≥ 0.15`; else `ambiguous` if any `≥ 0.50`; else foreign → near → case. Thresholds live in `reconciliation_settings.data["rules"]` with these defaults and `rules_version = "r1b-1"`.
- `flow_amount` is the ledger sign; `LedgerEntry.amount` is already signed the same way (expense −, income +, transfer_in +, transfer_out −, fee −, refund +, reward +).
- Services never commit; routers do. Commits carry `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; the PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- No owner financial data in tests; synthetic only. Every new table column needs a migration + `LEDGER_TABLES`/parity updates as in R1a (this plan adds none; it adds settings keys only).

## Rulings carried from R1a / the spec
- **No service hooks in R1b** (`coverage.touch`): the R1a triggers are the completeness net; the sweep runs at reconcile, at `GET` (read-only flag), via `POST /reconciliation/sweep` (worker, daily) and in R1c before apply/confirm. Service hooks are deferred to R1c if the UX needs immediacy.
- **"live" event = `current_line_id IS NOT NULL`** (R1a §17).
- **Installments** match only through a confirmed `installment_plan_map` row (C5); the `link_installment_plan` action is R1c, so in R1b an installment line without a mapping is a `line_unmatched` case with `context.hint = "installment_unmapped"` and the candidate definitions listed.
- **R8 bank-only lines** become `line_unmatched` cases with `context.hint = "bank_only"` and `context.pattern` (the policy/proposal that creates the system entry is R1c).
- **Deferred minors from R1a carried here:** merchant normaliser Unicode classes (kana, accented Latin) and single-character tokens (Task 2 touches `merchant.py`); `enqueue_run` docstring; cast-free jsonb comparison in the kill switch is left to R1c's migration if one is needed; `GET /settings/reconciliation` response model (Task 7); race-test cleanup hardening (Task 8 touches the helper).

## Review Focus

1. A split group whose members sum to a line must be claimed as a whole, members reserved, and a second line must not claim any member — pinned in Task 2 (pure) and Task 3 (DB unique index).
2. Two same-day same-amount standalone candidates with no merchant signal must produce `ambiguous`, never a claim — Task 2.
3. Editing a claimed entry's amount after reconcile must, on the next sweep, release the claim, reopen a case (live) and refresh counts; the same edit on a historical statement must release the claim without a case — Task 4.
4. A re-parse that changes a claimed line's merchant (`changed`) must quarantine the event, release its coverage and open `parse_review`; a whitespace-only change (`normalised`) must transfer coverage untouched — Task 5.
5. A card line with `foreign_amount` equal to the entry's original amount and a TWD residual exactly equal to the card's FX fee must NOT be consumed (no fee child yet) and must open `amount_delta` with `fee_expected` and `residual` equal — Task 2/Task 6.

---

### Task 1: Branch and baseline

- [ ] **Step 1:** worktree `/home/opc/workspace/home-hub-reconcile` on `feat/reconciliation-r1b` from `origin/main` (63eb131); `.venv` and `.env` link exist.
- [ ] **Step 2:** `.venv/bin/pytest -q` → 1224 passed, 6 warnings.
- [ ] **Step 3:** no commit.

---

### Task 2: Pure matching module

**Files:**
- Create: `services/accounting-service/app/services/statements/matching.py`
- Modify: `services/accounting-service/app/services/statements/merchant.py` (Unicode token classes)
- Test: `services/accounting-service/tests/unit/test_statement_matching.py`, extend `tests/unit/test_statement_merchant.py`

**Interfaces:**
- Consumes: `merchant.tokens`, `merchant.normalise`.
- Produces (all frozen dataclasses unless noted):
  - `matching.Line(id, event_id, posted_date, txn_date, flow: Decimal, foreign_amount, foreign_currency, line_kind, merchant_norm, installment_seq, installment_total)`
  - `matching.Entry(id, kind, flow: Decimal, posted_date, entry_date, name, merchant, original_amount, original_currency, account_id, group_id, parent_entry_id, is_settlement, refunds_entry_id, transfer_group_id, transfer_peer_is_card: bool | None, children: tuple[Child, ...], instance: InstanceRef | None)`; `Child(id, kind, flow)`; `InstanceRef(definition_id, seq, times)`
  - `matching.Group(id, kind, member_ids: tuple[int, ...])`
  - `matching.Rules(exact_window_days=3, foreign_window_days=5, near_window_days=5, near_tolerance_abs=Decimal("10"), near_tolerance_pct=Decimal("0.03"), accept=Decimal("0.80"), margin=Decimal("0.15"), ambiguous_floor=Decimal("0.50"), candidate_window_days=10)`
  - `matching.Representation(kind: str, rows: tuple[Row, ...], score: Decimal, rule: str, reasons: tuple[str, ...])`; `Row(entry_id, role, flow, group_id)` with role ∈ `principal|member|child`
  - `matching.Claim(line_id, representation)`; `matching.Case(line_id | None, entry_id | None, kind, candidates: tuple[dict, ...], context: dict)`
  - `matching.match(statement_kind: str, lines: list[Line], entries: list[Entry], groups: dict[int, Group], plan_map: dict[str, int], fee_expected: Callable[[Entry], Decimal | None], rules: Rules, account_currency: str) -> MatchResult(claims: list[Claim], cases: list[Case], unmatched_entry_ids: list[int], explained: dict[int, str])`
  - `matching.eligible_kinds(statement_kind, line_kind) -> frozenset[str]` (the §6.2 gate table)
  - `matching.conserved(rows, flow) -> bool`

Semantics (§6.2–6.3, binding):
- Gate first. For `installment` lines: admissible only through `plan_map[f"{merchant_norm}|{installment_total}|{abs(flow)}"]` → definition id, then the entry whose `instance.definition_id` matches and `instance.seq == installment_seq` and `flow` equal; no mapping → case `line_unmatched` with `context.hint="installment_unmapped"`, `candidates` = definition ids of entries whose instance has `times == installment_total` and equal flow.
- `purchase` lines: standalone `expense` entries not in any group and without an instance; complete groups (every member in the candidate set and unclaimed) as two representations (members only; members + fee/discount children); never partial groups; never installment members.
- Representations are collected across all gates, each must be conserved (`Σ rows.flow == line.flow`), scored, then decided once per line in print order (seq). Rows claimed by an earlier line are removed from later candidates (`claimed` set carried through the loop); a representation that would reuse a claimed row is dropped.
- `score = amount_term + date_term + text_term`, `date_term` uses `min(|posted_date − entry.posted_date|, |txn_date − entry.entry_date| if txn_date)`; `text_term = 0.10 × Jaccard(tokens(line.merchant_norm), tokens(normalise(entry.merchant or "")) | tokens(normalise(entry.name or "")))`.
- Decision: best ≥ accept and (no second or best − second ≥ margin) → claim with `rule` = the gate's name (`payment|refund|installment|group|group_children|exact|transfer`); else if any ≥ ambiguous_floor → `ambiguous` case with the top 5 candidates `[{entry_id|group_id, score, reasons}]`; else foreign: entries with `original_amount == line.foreign_amount` and matching currency within `foreign_window_days` → if `line.flow − entry.flow == −fee_expected(entry)` (within the currency quantum: TWD/JPY 1, else 0.01) → case `amount_delta` with `context={"residual": str, "fee_expected": str, "fee_matches": true, "entry_id": id}` (NOT a claim — the child does not exist yet); other residual → `amount_delta` with `fee_matches: false`; else near: same gate kinds, token overlap ≥ 0.5, `|delta| ≤ max(abs, pct×|flow|)`, within `near_window_days` → `amount_delta` case with `context.residual`; else bank-only pattern (`fee|interest|reward` line kinds whose `merchant_norm` matches `rules.bank_only_patterns` list: `年費`, `循環利息`, `現金回饋`, `跨行手續費`, `轉帳手續費`, `利息`) → `line_unmatched` with `hint="bank_only"`, `pattern`; else `line_unmatched`.
- Reverse side: `unmatched_entry_ids` = entries in the reverse population (caller passes `entries` with `in_reverse: bool`) not claimed; `explained[entry_id] = "deferred_next_period"` when `entry.posted_date > period_end − 2 days` (caller passes `period_end` via `Rules.period_end`).
- Bank statements: `deposit` ↔ `income|transfer_in|interest` + settlement inflows (`is_settlement and flow > 0`), `withdrawal` ↔ `expense|transfer_out|fee` + settlement outflows, `transfer_in/out` ↔ the same-kind leg; `payment` on a card ↔ `transfer_in` whose `transfer_peer_is_card is False`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_statement_matching.py
from datetime import date
from decimal import Decimal

from app.services.statements import matching as m

D = Decimal
R = m.Rules(period_end=date(2026, 9, 30))


def L(i, posted, flow, kind="purchase", merchant="全聯", **kw):
    return m.Line(id=i, event_id=100 + i, posted_date=posted, txn_date=kw.get("txn_date"), flow=D(flow),
                  foreign_amount=kw.get("foreign_amount"), foreign_currency=kw.get("foreign_currency"), line_kind=kind,
                  merchant_norm=merchant, installment_seq=kw.get("seq"), installment_total=kw.get("total"))


def E(i, posted, flow, kind="expense", merchant="全聯", **kw):
    return m.Entry(id=i, kind=kind, flow=D(flow), posted_date=posted, entry_date=kw.get("entry_date", posted),
                   name=kw.get("name"), merchant=merchant, original_amount=kw.get("original_amount"),
                   original_currency=kw.get("original_currency"), account_id=kw.get("account_id", 1),
                   group_id=kw.get("group_id"), parent_entry_id=kw.get("parent_entry_id"),
                   is_settlement=kw.get("is_settlement", False), refunds_entry_id=kw.get("refunds_entry_id"),
                   transfer_group_id=kw.get("transfer_group_id"), transfer_peer_is_card=kw.get("peer_is_card"),
                   children=tuple(kw.get("children", ())), instance=kw.get("instance"), in_reverse=kw.get("in_reverse", True))


def run(lines, entries, groups=None, plan_map=None, fee=lambda e: None, kind="card"):
    return m.match(kind, lines, entries, groups or {}, plan_map or {}, fee, R, "TWD")


def test_exact_match_claims_standalone_expense():
    res = run([L(1, date(2026, 9, 3), "-580")], [E(10, date(2026, 9, 3), "-580")])
    assert [(c.line_id, c.representation.rule, [r.entry_id for r in c.representation.rows]) for c in res.claims] == [(1, "exact", [10])]
    assert res.cases == [] and res.unmatched_entry_ids == []


def test_two_identical_candidates_without_merchant_signal_are_ambiguous():
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 3), "-580", merchant="A"), E(11, date(2026, 9, 3), "-580", merchant="B")])
    assert res.claims == [] and res.cases[0].kind == "ambiguous" and {c["entry_id"] for c in res.cases[0].candidates} == {10, 11}


def test_merchant_signal_breaks_the_tie():
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], [E(10, date(2026, 9, 3), "-580", merchant="全聯福利中心 大安"), E(11, date(2026, 9, 3), "-580", merchant="家樂福")])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_group_claims_every_member_and_blocks_a_second_line():
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(21, date(2026, 9, 3), "-300", group_id=5), E(22, date(2026, 9, 3), "-280", group_id=5)]
    res = run([L(1, date(2026, 9, 3), "-580"), L(2, date(2026, 9, 3), "-300")], entries, groups={5: g})
    claim = res.claims[0]
    assert claim.representation.rule == "group" and sorted(r.entry_id for r in claim.representation.rows) == [21, 22]
    assert [c.line_id for c in res.cases] == [2] and res.cases[0].kind == "line_unmatched"


def test_group_with_children_is_a_separate_representation():
    g = m.Group(id=5, kind="split", member_ids=(21,))
    fee = m.Child(id=31, kind="fee", flow=D("-2"))
    entries = [E(21, date(2026, 9, 3), "-100", group_id=5, children=(fee,))]
    res = run([L(1, date(2026, 9, 3), "-102")], entries, groups={5: g})
    assert res.claims[0].representation.rule == "group_children" and sorted(r.entry_id for r in res.claims[0].representation.rows) == [21, 31]
    res2 = run([L(1, date(2026, 9, 3), "-100"), L(2, date(2026, 9, 3), "-2", kind="fee")], entries, groups={5: g})
    assert [c.representation.rule for c in res2.claims] == ["group", "exact"]


def test_partial_group_is_never_admissible():
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(21, date(2026, 9, 3), "-300", group_id=5)]  # member 22 not in the candidate window
    res = run([L(1, date(2026, 9, 3), "-300")], entries, groups={5: g})
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"


def test_installment_needs_a_confirmed_plan():
    inst = m.InstanceRef(definition_id=7, seq=2, times=12)
    entries = [E(40, date(2026, 9, 5), "-1000", instance=inst)]
    line = L(1, date(2026, 9, 5), "-1000", kind="installment", merchant="APPLE", seq=2, total=12)
    res = run([line], entries)
    assert res.claims == [] and res.cases[0].context["hint"] == "installment_unmapped" and res.cases[0].candidates == ({"definition_id": 7},)
    res2 = run([line], entries, plan_map={"APPLE|12|1000.0000": 7})
    assert res2.claims[0].representation.rule == "installment"


def test_wrong_plan_with_equal_numbers_stays_a_case():
    entries = [E(40, date(2026, 9, 5), "-1000", instance=m.InstanceRef(definition_id=8, seq=2, times=12))]
    res = run([L(1, date(2026, 9, 5), "-1000", kind="installment", merchant="APPLE", seq=2, total=12)], entries, plan_map={"APPLE|12|1000.0000": 7})
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"


def test_payment_matches_transfer_in_from_a_non_card():
    entries = [E(50, date(2026, 9, 10), "5000", kind="transfer_in", transfer_group_id="t1", peer_is_card=False)]
    res = run([L(1, date(2026, 9, 10), "5000", kind="payment", merchant="繳款")], entries)
    assert res.claims[0].representation.rule == "payment"


def test_orphan_refund_is_eligible():
    entries = [E(60, date(2026, 9, 12), "200", kind="refund", refunds_entry_id=None)]
    res = run([L(1, date(2026, 9, 12), "200", kind="refund")], entries)
    assert res.claims[0].representation.rule == "refund"


def test_foreign_residual_equal_to_fee_is_a_delta_case_not_a_claim():
    entries = [E(70, date(2026, 9, 8), "-660", original_amount=D("-3000"), original_currency="JPY")]
    res = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D("-3000"), foreign_currency="JPY")], entries, fee=lambda e: D("10"))
    case = res.cases[0]
    assert res.claims == [] and case.kind == "amount_delta" and case.context["fee_matches"] is True and case.context["residual"] == "-10.0000"


def test_foreign_residual_not_equal_to_fee():
    entries = [E(70, date(2026, 9, 8), "-660", original_amount=D("-3000"), original_currency="JPY")]
    res = run([L(1, date(2026, 9, 8), "-690", foreign_amount=D("-3000"), foreign_currency="JPY")], entries, fee=lambda e: D("10"))
    assert res.cases[0].kind == "amount_delta" and res.cases[0].context["fee_matches"] is False


def test_near_match_is_always_a_case():
    res = run([L(1, date(2026, 9, 3), "-585", merchant="全聯 大安")], [E(10, date(2026, 9, 3), "-580", merchant="全聯 大安")])
    assert res.claims == [] and res.cases[0].kind == "amount_delta" and res.cases[0].context["residual"] == "-5.0000"


def test_bank_only_line_is_hinted():
    res = run([L(1, date(2026, 9, 30), "-5", kind="fee", merchant="跨行手續費")], [])
    assert res.cases[0].kind == "line_unmatched" and res.cases[0].context["hint"] == "bank_only"


def test_reverse_population_and_deferral():
    entries = [E(10, date(2026, 9, 3), "-580"), E(11, date(2026, 9, 29), "-90")]
    res = run([L(1, date(2026, 9, 3), "-580")], entries)
    assert res.unmatched_entry_ids == [11] and res.explained == {11: "deferred_next_period"}


def test_bank_deposit_matches_income_and_settlement_inflow():
    entries = [E(10, date(2026, 9, 5), "30000", kind="income"), E(11, date(2026, 9, 6), "500", kind="receivable", is_settlement=True)]
    res = run([L(1, date(2026, 9, 5), "30000", kind="deposit"), L(2, date(2026, 9, 6), "500", kind="deposit")], entries, kind="bank")
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10, 11]


def test_claims_are_conserved():
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(21, date(2026, 9, 3), "-300", group_id=5), E(22, date(2026, 9, 3), "-281", group_id=5)]
    res = run([L(1, date(2026, 9, 3), "-580")], entries, groups={5: g})
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"
```

Extend `tests/unit/test_statement_merchant.py`:

```python
def test_tokens_keep_kana_accents_and_single_cjk():
    assert merchant.normalise("ユニクロ 新宿") == "ユニクロ 新宿"
    assert merchant.tokens("CAFÉ NÉRO") == frozenset({"CAFÉ", "NÉRO"})
    assert merchant.tokens("STORE A") == frozenset({"STORE", "A"})
```

- [ ] **Step 2: Run** → FAIL (`ModuleNotFoundError`, and the merchant test fails on kana).

- [ ] **Step 3: Implement**

`merchant.py`: `_NON_TOKEN = re.compile(r"[^\w]+", re.UNICODE)` with `_` added to the drop set (`[^\w]|_`), keep the prefix table; `tokens` keeps single-character tokens when they are CJK/kana (`len(part) >= 2 or unicodedata.category(part)[0] == "L" and ord(part) > 0x2E80`) and Latin single letters (per the R1a review: `STORE A` vs `STORE B` must differ) — simplest: `tokens = {p for p in norm.split(" ") if p}`; the single-character rule from R1a is dropped (the R1a tests `tokens("PAYPAL SPOTIFY 7") == {"PAYPAL","SPOTIFY"}` change to include `"7"`; update that test).

`matching.py` (complete):

```python
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
    if line.line_kind == "purchase":
        for g in groups.values():
            if g.kind != "split":
                continue
            members = [by_id.get(i) for i in g.member_ids]
            if any(m is None or m.id in claimed for m in members):
                continue  # partial or already reserved groups are never admissible
            member_rows = tuple(Row(m.id, "member", m.flow, g.id) for m in members)
            child_rows = tuple(Row(c.id, "child", c.flow, g.id) for m in members for c in m.children if c.id not in claimed)
            for variant, rows in (("group", member_rows), ("group_children", member_rows + child_rows)):
                if rows and conserved(rows, line.flow):
                    score, reasons = _score(line, tuple(members), rules.exact_window_days)
                    if score > ZERO:
                        out.append(Representation(variant, rows, score, variant, reasons))
    return [r for r in out if conserved(r.rows, line.flow)]


def _quantum(currency: str) -> Decimal:
    return Decimal("1") if currency in ("TWD", "JPY") else Decimal("0.01")


def _foreign_case(line: Line, entries: list[Entry], claimed: set[int], fee_expected, rules: Rules, currency: str) -> Case | None:
    if line.foreign_amount is None:
        return None
    for e in entries:
        if e.id in claimed or e.original_amount is None or e.original_currency != line.foreign_currency:
            continue
        if e.original_amount.quantize(Q) != line.foreign_amount.quantize(Q) or _date_distance(line, e) > rules.foreign_window_days:
            continue
        residual = (line.flow - e.flow).quantize(Q)
        fee = fee_expected(e)
        matches = fee is not None and abs(residual + fee).quantize(Q) <= _quantum(currency)
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
            cands = tuple({"entry_id": r.rows[0].entry_id if r.kind == "standalone" else None, "group_id": r.rows[0].group_id,
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
        case = _foreign_case(line, entries, claimed, fee_expected, rules, account_currency) or _near_case(statement_kind, line, entries, claimed, rules)
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
```

- [ ] **Step 4: Run** `.venv/bin/pytest -q tests/unit/test_statement_matching.py tests/unit/test_statement_merchant.py tests/unit/test_statement_derive.py` → PASS (adjust the R1a `tokens` test to the new single-token rule).
- [ ] **Step 5: Commit** `feat(accounting): pure statement matching — gates, representations, conservation, cases`.

---

### Task 3: Coverage service — claims, release, conservation

**Files:**
- Create: `services/accounting-service/app/services/coverage_service.py`
- Test: `services/accounting-service/tests/integration/test_coverage_service.py`

**Interfaces:**
- `snapshot_entry(entry: LedgerEntry) -> dict` — `{amount, currency, original_amount, original_currency, entry_date, posted_date, account_id, kind, group_id, parent_entry_id, transfer_group_id, refunds_entry_id, settles_entry_id, is_settlement, name, merchant}` (dates/decimals as strings).
- `write_claim(db, statement, line: StatementLine, rows: list[matching.Row], *, entries_by_id: dict[int, LedgerEntry], match_kind: str, match_rule: str, run_id: int | None) -> list[StatementCoverage]` — inserts one row per `Row` with `snapshot`, asserts `conserved` against `line.flow_amount` (raises `ConflictError("coverage not conserved")`), relies on the DB unique index for the global claim (an `IntegrityError` on `ux_statement_coverage_active_entry` is converted to `CodedConflictError("duplicate_claim")`).
- `release_line(db, statement, line_id, *, reason: str) -> int` — sets the line's active rows `stale` with `stale_reason`.
- `active_rows(db, statement_id) -> dict[line_id, list[StatementCoverage]]`.
- `claimed_entry_ids(db) -> set[int]` (all active rows, any statement) — used by the candidate loader to exclude rows claimed by other statements.
- `assert_conserved(db, statement) -> None` — recomputes Σ snapshot flow per line vs `flow_amount`; raises on mismatch (called before every commit that wrote coverage; the router calls it after `reconcile`).

- [ ] **Step 1: Tests** (abridged): writing a conserved claim stores rows with snapshots; a non-conserved claim raises; a second claim on an already-claimed entry raises `duplicate_claim`; `release_line` marks stale and frees the entry for a new claim; `assert_conserved` passes/fails accordingly.
- [ ] **Step 2–4:** RED → implement → GREEN → commit `feat(accounting): coverage service with conservation and unique active claims`.

---

### Task 4: Dirty sweep

**Files:**
- Modify: `services/accounting-service/app/services/coverage_service.py` (add `sweep`)
- Test: `services/accounting-service/tests/integration/test_coverage_sweep.py`

**Interfaces:**
- `sweep(db, statement: AccountStatement, *, run_id=None) -> SweepResult(stale_lines: list[int], reopened_cases: list[int], recheck: bool, swept_through: int)`
  - Precondition: caller holds the statement row lock (`FOR UPDATE`).
  - Reads `coverage_dirty` rows with `id > statement.swept_through_event_id` that touch the statement: `kind='entry'` and (`old_account_id` or `new_account_id` in the participating set now) or `row_id` has an active coverage row in this statement; `kind='group'` and the group id appears in this statement's active coverage; `kind='account'` with `row_id` in the participating set.
  - Entry events: `update`/`delete` on a row with active coverage in this statement → `release_line(reason='dirty:<op>')` for that line, in live mode reopen the line's resolved case or create `recheck` (`context={"event_id": …, "op": …}`), in historical mode no case; `insert` with `new_date` inside `[period_start, period_end]` → `statement.needs_recheck = True` (live) ; account events → `needs_recheck = True` (live).
  - Skips events whose `action_id` belongs to an applied `reconciliation_action` of this statement (R1c writes those; in R1b the set is empty — implement the query anyway).
  - Advances `swept_through_event_id` to the max id seen in the scan (including events not touching the statement, so the watermark never re-reads them).
  - Never deletes events.
- `sweep_pending(db) -> list[int]` — statement ids with `exists(coverage_dirty.id > watermark touching the account)`, used by the worker's daily `POST /reconciliation/sweep`.

- [ ] **Step 1: Tests** (each uses real ledger writes through the existing services or raw SQL where the design names a path): amount edit on a claimed entry (live → claim stale + `recheck` case; historical → stale, no case); delete of a claimed entry (`entry_id` NULL on the row via `SET NULL`, row stale, snapshot kept); split member detachment (group event + entry update → group claim released as a whole); `delete_moze_entries`-style bulk DELETE with `synchronize_session=False` (rows stale); account `opening_balance` edit → `needs_recheck`; insert into a reconciled period → `needs_recheck`; cross-period move of an UNCLAIMED entry → no release, `needs_recheck` on both statements whose periods are touched (old/new date); self-event skip (insert a fake `reconciliation_action` row + set `app.reconciliation_action_id` before the write → no release); watermark advances past unrelated events; `dirty_enabled` false → nothing to sweep.
- [ ] **Step 2–4:** RED → implement → GREEN → commit `feat(accounting): dirty-event sweep with watermark, release and recheck`.

---

### Task 5: Lineage coverage transfer and quarantine

**Files:**
- Modify: `services/accounting-service/app/services/statement_revision_service.py`
- Test: extend `tests/integration/test_statement_ingest_service.py`

**Interfaces:**
- In `_write_lines_and_lineage`, when the revision becomes current: for `identical` pairings move the old line's active coverage rows to the new line (`line_id` updated, `transferred=True` on the lineage row); for `normalised` the same plus `line_flagged=True` on the new line's event (`StatementEvent.flag = 'text_changed'` — add a nullable `String(32)` column `flag` on `statement_event` via a small migration `e2a9c4d1b7f0_statement_event_flag` with the usual parity/LEDGER_TABLES updates; downgrade drops the column); for `changed`/`unpaired-old` with active coverage: release the coverage (`reason='lineage:<equivalence>'`), set the old event `quarantined` (instead of `retired`), open `parse_review` with `context={"quarantined_event_id": …, "equivalence": …}` (live only); without coverage: `retired` as today.
- A `parse_review` opened by quarantine blocks reconcile of that statement until resolved (R1c action); in R1b `reconcile` skips statements with an open `parse_review` and returns `{"skipped": "parse_review"}`.

- [ ] **Step 1: Tests:** identical re-parse keeps the claim (rows now point at the new line, lineage `transferred`); normalised → same + event flag; changed with coverage → coverage stale, event quarantined, `parse_review` case (live), none (historical); changed without coverage → retired (R1a behaviour kept).
- [ ] **Step 2–4:** RED → migration + implement → GREEN (incl. `test_migration` head pins → `R1B_HEAD`) → commit `feat(accounting): coverage transfer and quarantine across re-parses`.

---

### Task 6: Reconciliation orchestration

**Files:**
- Create: `services/accounting-service/app/services/reconciliation_service.py`
- Modify: `statement_revision_service.py` (call `reconcile` when the revision becomes current and `guardrail_ok`), `settings_service.py` (`rules` defaults + `rules_version`)
- Test: `tests/integration/test_reconciliation_service.py`

**Interfaces:**
- `participating_accounts(db, account_id) -> list[int]` — the account plus accounts with `combined_account_id == account_id`.
- `load_candidates(db, statement, accounts) -> tuple[list[matching.Entry], dict[int, matching.Group]]` — entries of those accounts with `posted_date` (fallback `entry_date`) in `[period_start − 10 d, period_end + 10 d]`, excluding `balance_adjustment`, excluding rows in `coverage_service.claimed_entry_ids()` except rows claimed by THIS statement (they are released at the start of a reconcile), `reward` only when the statement has reward lines; children (`parent_entry_id` set) attached to their parent as `Child`; `transfer_peer_is_card` from the paired leg's account `is_credit`; `instance` from `schedule_instance.posted_entry_ids @> [id]` joined to `schedule_definition` (`definition_id, seq, times`); `in_reverse` = `posted_date ∈ [period_start, period_end]`; groups = `entry_group` rows referenced by the entries with `member_ids` = ALL members of the group (not only those in the window — a group with a member outside the window is partial and inadmissible by construction).
- `plan_map(db, account_id) -> dict[str, int]` from `installment_plan_map`.
- `reconcile(db, statement_id, *, run_id=None) -> ReconcileResult(claims: int, cases_opened: list[int], explained: int, unmatched_entries: int, skipped: str | None)`:
  1. `take_import_key_shared`; lock the statement `FOR UPDATE`; if `current_revision_id is None` or the current revision's `guardrail_ok` is false → `skipped="no_current_revision"`; if an open `parse_review` exists → `skipped="parse_review"`.
  2. `coverage_service.sweep` first.
  3. Release this statement's active claims (`reason='reconcile'`) and supersede its open `line_unmatched|ambiguous|amount_delta|entry_unmatched|duplicate_claim|balance_gap` cases (dismissed ones stay dismissed; deferred explanations are re-derived).
  4. Load lines (current revision), candidates, groups, plan map, rules from settings; `fee_expected = lambda e: proposed_fx_fee(account, e.amount)`.
  5. Lock groups (ascending) then the candidate entries with legs in one ordered `FOR UPDATE` (`locked_with_legs`-style select over the candidate ids), re-read snapshots after locking.
  6. `matching.match(...)`; write claims; write cases only when `statement.mode == 'live'` (dismissed cases with the same `(event_id, kind)` are not reopened); deferrals: for each `explained[entry] == "deferred_next_period"` create/refresh a resolved `entry_unmatched` case with `explanation='deferred_next_period'`, `deferred_to_period_end` = next period end computed from the account (`closing_day` → the next closing date after `period_end`; bank → last day of next month), `deferred_to_statement_id` when that statement exists; cross-account (R10): for each unmatched entry outside the participating set's combined children, look for an unmatched line on another live statement with equal flow ±3 d → `entry_unmatched` case with `context.hint="move_account"` and the candidate statement/line; otherwise `entry_unmatched`.
  7. Bank statements: `balance_gap = statement_total − ledger_service.account_balance(db, account_id, as_of=period_end)`; non-zero → `balance_gap` case (live) with `context.gap`.
  8. `assert_conserved`; recount: `matched_count` = lines with active coverage, `explained_count` = deferred entries + dismissed cases, `open_case_count`.
- `reconcile_pending(db, *, run_id=None) -> dict` — runs `reconcile` for every live statement returned by `sweep_pending` plus those with `needs_recheck`; used by the daily worker route.

- [ ] **Step 1: Tests:** end-to-end with `seed`: a card statement with purchases/payment/refund/group/foreign → expected claims/cases; a historical statement gets coverage but no cases; re-reconcile is idempotent (same rows, same counts); a claim held by another statement is not stolen; combined child card spend matches on the master statement; deferral creates the forward link once the next statement exists; bank `balance_gap`; `needs_recheck` cleared after a reconcile; lock-order test with `tests/helpers.race`: reconcile vs `update_entry` on a candidate (no deadlock, edit blocks until reconcile commits).
- [ ] **Step 2–4:** RED → implement → GREEN → commit `feat(accounting): reconciliation orchestration — sweep, match, coverage, cases, deferrals, balance gaps`.

---

### Task 7: Routes and read model

**Files:**
- Modify: `app/routers/statements.py`, `app/schemas/statements.py`, `app/services/statement_ingest_service.py::get_statement`, `settings_service.py`
- Test: extend `tests/integration/test_statement_ingest_api.py`, `test_scope_enforcement.py` (new mutation routes), `test_edit_lock.py` inventory

**Routes:**
| method | path | scope | status |
|---|---|---|---|
| POST | `/accounts/{account_id}/statements/{statement_id}/reconcile` | `write` | 200 `ReconcileOut(claims, cases_opened, explained, unmatched_entries, skipped)` |
| POST | `/reconciliation/sweep` | `ingest` | 200 `{statements: n, claims: n, cases_opened: n}` (worker daily; body `Lease`) |
| GET | `/reconciliation/cases?status=&account_id=` | `read` | 200 `list[CaseOut]` with `candidates`/`context` |
`get_statement` gains per line `coverage: [{entry_id, group_id, role, flow, match_rule, status}]`, `matched: bool`, and the statement's `matched_count/explained_count/open_case_count`; `stale_events_pending` unchanged. `GET /settings/reconciliation` gets `ReconciliationSettingsOut(account_map, dirty_enabled, rules, rules_version, version)` (R1a minor) and PUT accepts `rules` (validated keys/types).

- [ ] **Step 1: Tests:** reconcile route requires `write` (hermes 403, worker 403, spa 200); sweep requires `ingest`; cases listing `read`; detail shows coverage; the scope matrix picks up the new routes automatically; edit-lock inventory updated.
- [ ] **Step 2–4:** RED → implement → GREEN → commit `feat(accounting): reconcile, sweep and case routes; coverage in the statement read model`.

---

### Task 8: Hardening carried from R1a

**Files:** `tests/integration/test_statement_lease_races.py` (cleanup guard: join only if started; close the contender session in its own thread; bounded cancel via `pg_cancel_backend` on timeout), `app/services/statement_ingest_service.py` (`enqueue_run` docstring), `tests/helpers.py` (`wait_until_blocked` optional `blocker_pid` check via `pg_blocking_pids`).
- [ ] RED/GREEN → commit `test(accounting): race-test cleanup hardening and blocker-pid check`.

---

### Task 9: openspec delta and docs

- [ ] `openspec/changes/add-statement-reconciliation-r1b/` (proposal, tasks, `.openspec.yaml`, `specs/accounting-reconciliation/spec.md` ADDED: Eligibility gates; Representations and scoring; Monetary conservation and unique claims; Dirty sweep; Coverage transfer and quarantine; Deferral and cross-account hints; Bank balance gap; Reconcile and sweep routes; MODIFIED: Events and lineage (quarantine), Statement read model) with scenarios from the tests; README: `rules` settings, the daily sweep.
- [ ] `openspec validate add-statement-reconciliation-r1b` → valid; commit.

---

### Task 10: Whole-branch verification and PR

- [ ] `.venv/bin/pytest -q` green; `alembic heads` = the Task 5 revision; diff confined to the service/openspec/docs; CodeRabbit CLI vs main; final whole-branch review; PR "feat(accounting): statement reconciliation R1b — matching, coverage, dirty sweep" with the deploy note "migration adds one nullable column; no env change; triggers still gated"; Multica non-author review; owner merges.

## Self-review notes
- Spec coverage: §6.1 populations (Task 6), §6.2 gates (Task 2), §6.3 representations/scoring/foreign/near/bank-only/deferral/cross-account (Tasks 2, 6), §6.4 bank gap (Task 6), §4.6 coverage + conservation + sweep (Tasks 3, 4), §5.7 transfer/quarantine (Task 5), §8 reconcile route (Task 7). Not here: actions/proposals/policy/confirm/apply (R1c), worker (R2).
- Placeholders: Tasks 3, 4, 6 describe tests by behaviour rather than full code; the matching module and its tests are complete. The implementer of each gets the interface block plus the spec sections; the reviewer gate checks the listed behaviours one by one.
- Type consistency: `matching.Row` (`entry_id, role, flow, group_id`) is what `coverage_service.write_claim` consumes; `Entry.children` are `Child` rows the group variant turns into `child` rows; `Rules.period_end` is set by the orchestrator from the statement.
