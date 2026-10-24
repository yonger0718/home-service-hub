"""Reconciliation action registry (R1c): `ACTIONS` maps an action name to its implementation (filled by each action
module: Task 4 `match`/`unmatch`, later tasks the rest) and `allowed(case)` answers which registered actions a case
offers — by kind (`KIND_ACTIONS`, the spec's table), narrowed by the case context (`CONDITIONS`), only while the
case is open or proposed. The engine lives in `engine.py` (not imported here, so action modules can import the
package without a cycle)."""
from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import object_session

from app.models import AccountStatement, ReconciliationCase, StatementLine
from app.services import reconciliation_service as rec
from app.services.coverage_service import participating_accounts

from .base import Action

ACTIONS: dict[str, Action] = {}

OPEN_STATUSES = ("open", "proposed")
COVERAGE_ACTIONS = frozenset({"match", "unmatch"})  # allowed on historical statements (ruling 8)

# The order is the order `allowed` lists them in.
KIND_ACTIONS: dict[str, tuple[str, ...]] = {
    "line_unmatched": ("match", "create_entry", "create_system_entry", "backfill_entry", "create_refund", "link_installment_plan",
                       "split_entry", "dismiss"),
    "ambiguous": ("match", "adjust_amount", "dismiss"),  # adjust_amount on a listed entry candidate (2026-10-24)
    "amount_delta": ("match", "adjust_amount", "attach_fee", "split_entry", "dismiss"),
    "entry_unmatched": ("defer_next_period", "move_account", "replace_stand_in", "dismiss"),
    "balance_gap": ("balance_adjustment_asof", "anchor_opening", "dismiss"),
    "duplicate_claim": ("unmatch", "dismiss"),
    "recheck": ("match", "unmatch", "dismiss"),
    "parse_review": ("dismiss",),  # resolved by a new revision; the owner may only dismiss it
    "statement_conflict": ("resolve_conflict",),  # ruling 34: a dismissed conflict would leave conflict_open set
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


PEER_HINT = "needs_peer"


def _no_peer_hint(case: ReconciliationCase) -> bool:  # R6b: a transfer/payment line gets no single-leg entry
    return _context(case).get("hint") != PEER_HINT


STAND_IN_DELETED_HINT = "stand_in_deleted"


def backfill_eligibility(case: ReconciliationCase) -> str | None:
    """R6b.1 owner column: no hint (or the owner-deleted hint), no candidates, a line kind the map books."""
    from app.services.reconciliation_actions.ledger_actions import backfill_kind  # local: ledger_actions imports this module
    ctx = _context(case)
    if ctx.get("hint") not in (None, STAND_IN_DELETED_HINT) or case.candidates:
        return None
    db = object_session(case)
    statement = None if db is None else db.get(AccountStatement, case.statement_id)
    kind = _line_kind(case)
    return None if statement is None or kind is None else backfill_kind(statement.kind, kind)


def _bank_statement(case: ReconciliationCase) -> bool:
    db = object_session(case)
    statement = None if db is None else db.get(AccountStatement, case.statement_id)
    return statement is not None and statement.kind == "bank"


def _single_account_gap(case: ReconciliationCase) -> bool:
    """`balance_adjustment_asof` books on the statement's account: only when the gap holds that account alone (a bank
    gap, or a card with no co-branded account in the statement's currency; otherwise the residual may sit on either)."""
    db = object_session(case)
    statement = None if db is None else db.get(AccountStatement, case.statement_id)
    return statement is not None and rec.gap_accounts(
        db, statement, participating_accounts(db, statement.account_id)) == [statement.account_id]


def _anchored(case: ReconciliationCase) -> bool:
    from app.services.reconciliation_actions import structural_actions  # local: it imports this package
    db = object_session(case)
    statement = None if db is None else db.get(AccountStatement, case.statement_id)
    return statement is not None and structural_actions.anchor_applied(db, statement.account_id)


CONDITIONS: dict[tuple[str, str], Callable[[ReconciliationCase], bool]] = {
    ("line_unmatched", "create_entry"): _no_peer_hint,
    ("line_unmatched", "create_system_entry"): _system_line,
    ("line_unmatched", "backfill_entry"): lambda case: backfill_eligibility(case) is not None,
    ("line_unmatched", "create_refund"): _refund_line,
    ("line_unmatched", "link_installment_plan"): lambda case: _context(case).get("hint") == "installment_unmapped",
    ("line_unmatched", "split_entry"): _names_an_entry,
    ("balance_gap", "balance_adjustment_asof"): _single_account_gap,
    ("balance_gap", "anchor_opening"): lambda case: _bank_statement(case) and not _anchored(case),
    ("amount_delta", "attach_fee"): lambda case: _context(case).get("fee_matches") is True,
    ("entry_unmatched", "replace_stand_in"):
        lambda case: _context(case).get("hint") in ("shadowed_by_stand_in", "orphaned_stand_in"),
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


# register every action module
from . import case_actions, coverage_actions, ledger_actions, structural_actions  # noqa: E402,F401
