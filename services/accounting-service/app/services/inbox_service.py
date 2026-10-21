"""Reconciliation inbox: question classifier, group key and button rules.

See docs/superpowers/specs/2026-10-21-reconciliation-inbox-design.md (sections 4.1-4.3).
The functions here are pure; the DB read model lives further down this module.
"""
import re
from decimal import Decimal

SETTLEMENT_LABELS: tuple[str, ...] = ("交割股款", "申購股款")
QUESTIONS = ("bank_only", "transfer", "ledger_only", "amount_differs", "date_differs",
             "ambiguous", "balance_gap", "settlement", "proposed", "other")
FOLDED = frozenset({"proposed", "balance_gap", "settlement", "other"})

BUTTONS_TO_ACTIONS = {
    "record": "create_entry",
    "match_candidate": "match",
    "accept_bank_amount": "adjust_amount",
    "defer": "defer_next_period",
    "dismiss": "dismiss",
    "anchor": "anchor_opening",
}


def pattern(merchant_norm: str) -> str:
    """Merchant text with digit runs removed and whitespace collapsed."""
    return " ".join(re.sub(r"\d+", "", merchant_norm).split())


def classify(*, kind: str, status: str, statement_kind: str, hint: str | None,
             merchant_norm: str | None, allowed: list[str]) -> str:
    """Map a reconciliation case to its inbox question."""
    if (statement_kind == "bank" and kind == "line_unmatched"
            and any(label in (merchant_norm or "") for label in SETTLEMENT_LABELS)):
        return "settlement"
    if status == "proposed":
        return "proposed"
    if hint == "needs_peer":
        return "transfer"
    if kind == "line_unmatched" and "create_entry" in allowed:
        return "bank_only"
    if kind == "entry_unmatched":
        return "ledger_only"
    if kind == "amount_delta" and hint == "date_drift":
        return "date_differs"
    if kind == "amount_delta":
        return "amount_differs"
    if kind == "ambiguous":
        return "ambiguous"
    if kind == "balance_gap":
        return "balance_gap"
    return "other"


def flow_sign(flow_amount: Decimal) -> str:
    return "-" if flow_amount < 0 else "+"


def group_key(question: str, account_id: int, line_kind: str, merchant_norm: str,
              flow_amount: Decimal) -> str | None:
    """Key merging identical bank-only cards; None when the case must stay single."""
    if question != "bank_only":
        return None
    pat = pattern(merchant_norm)
    if not pat:
        return None
    return f"{account_id}|{line_kind}|{pat}|{flow_amount:.4f}"


def buttons(question: str, allowed: list[str], *, adjustable: bool) -> list[str]:
    """Ordered button list for a question, limited to the registry's allowed actions."""
    if question == "proposed":
        return ["apply_proposal", "reject_proposal"]
    if question == "settlement":
        return []
    out: list[str] = []

    def add(button: str, needs: str | None = None, extra: bool = True) -> None:
        if extra and (needs is None or needs in allowed):
            out.append(button)

    if question == "bank_only":
        add("record", "create_entry")
        add("match_candidate", "match")
        add("dismiss", "dismiss")
    elif question in ("transfer", "ambiguous", "date_differs"):
        add("match_candidate", "match")
        add("dismiss")
    elif question == "ledger_only":
        add("defer", "defer_next_period")
        add("dismiss")
    elif question == "amount_differs":
        add("accept_bank_amount", "adjust_amount", adjustable)
        add("match_candidate", "match")
        add("dismiss")
    elif question == "balance_gap":
        add("anchor", "anchor_opening")
        add("dismiss")
    else:
        add("dismiss", "dismiss")
    return out


def learnable(question: str, button: str) -> bool:
    """Whether a click on this button may be remembered as an owner rule."""
    return ((question == "bank_only" and button in ("record", "dismiss"))
            or (question == "amount_differs" and button == "accept_bank_amount"))
