"""Reconciliation action registry (R1c): `ACTIONS` maps an action name to its implementation (filled by each action
module: Task 4 `match`/`unmatch`, later tasks the rest) and `allowed(case)` answers which registered actions a case
offers — by kind (`KIND_ACTIONS`, the spec's table), narrowed by the case context (`CONDITIONS`), only while the
case is open or proposed. The engine lives in `engine.py` (not imported here, so action modules can import the
package without a cycle)."""
from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import object_session

from app.models import ReconciliationCase, StatementLine

from .base import Action

ACTIONS: dict[str, Action] = {}

OPEN_STATUSES = ("open", "proposed")
COVERAGE_ACTIONS = frozenset({"match", "unmatch"})  # allowed on historical statements (ruling 8)

# The order is the order `allowed` lists them in.
KIND_ACTIONS: dict[str, tuple[str, ...]] = {
    "line_unmatched": ("match", "create_entry", "create_system_entry", "create_refund", "link_installment_plan",
                       "split_entry", "dismiss"),
    "ambiguous": ("match", "dismiss"),
    "amount_delta": ("match", "adjust_amount", "attach_fee", "split_entry", "dismiss"),
    "entry_unmatched": ("defer_next_period", "move_account", "dismiss"),
    "balance_gap": ("balance_adjustment_asof", "dismiss"),
    "duplicate_claim": ("unmatch", "dismiss"),
    "recheck": ("match", "unmatch", "dismiss"),
    "parse_review": ("dismiss",),  # resolved by a new revision; the owner may only dismiss it
    "statement_conflict": ("resolve_conflict", "dismiss"),
}


def _context(case: ReconciliationCase) -> dict:
    return case.context or {}


SYSTEM_LINE_KINDS = frozenset({"fee", "interest", "reward"})


def _line_kind(case: ReconciliationCase) -> str | None:
    db = object_session(case)
    if db is None or case.line_id is None:
        return None
    line = db.get(StatementLine, case.line_id)
    return None if line is None else line.line_kind


def _refund_line(case: ReconciliationCase) -> bool:  # card and bank refund lines alike
    return _line_kind(case) == "refund"


def _system_line(case: ReconciliationCase) -> bool:  # a bank-only hint, or any fee/interest/reward line
    return _context(case).get("hint") == "bank_only" or _line_kind(case) in SYSTEM_LINE_KINDS


def _names_an_entry(case: ReconciliationCase) -> bool:  # ruling 19: split on line_unmatched needs a named entry
    return bool(case.candidates) or _context(case).get("entry_id") is not None


CONDITIONS: dict[tuple[str, str], Callable[[ReconciliationCase], bool]] = {
    ("line_unmatched", "create_system_entry"): _system_line,
    ("line_unmatched", "create_refund"): _refund_line,
    ("line_unmatched", "link_installment_plan"): lambda case: _context(case).get("hint") == "installment_unmapped",
    ("line_unmatched", "split_entry"): _names_an_entry,
    ("amount_delta", "attach_fee"): lambda case: _context(case).get("fee_matches") is True,
}


def register(action: Action) -> Action:
    ACTIONS[action.name] = action
    return action


def allowed(case: ReconciliationCase) -> list[str]:
    """Registered actions the case offers now: [] unless open/proposed; by kind, narrowed by context."""
    if case.status not in OPEN_STATUSES:
        return []
    out = []
    for name in KIND_ACTIONS.get(case.kind, ()):
        action = ACTIONS.get(name)
        if action is None or case.kind not in action.case_kinds:
            continue
        condition = CONDITIONS.get((case.kind, name))
        if condition is None or condition(case):
            out.append(name)
    return out


from . import case_actions, coverage_actions, ledger_actions  # noqa: E402,F401  (register every action module)
