"""Reconciliation inbox: question classifier, group key and button rules.

See docs/superpowers/specs/2026-10-21-reconciliation-inbox-design.md (sections 4.1-4.3).
The functions here are pure; the DB read model lives further down this module.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Account, AccountStatement, Category, LedgerEntry, ReconciliationCase, ReconciliationOwnerRule, StatementLine,
)
from app.services import coverage_service, proposal_service, reconciliation_actions, split_protection
from app.services.errors import NotFoundError
from app.services.reconciliation_actions import base
from app.services.statements import matching

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
    if kind == "line_unmatched" and hint == "installment_unmapped":  # the registry still allows create_entry here
        return "other"
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


# --- DB read model (spec §4) ------------------------------------------------------------------------------------

CANDIDATE_LIMIT = 5


def _single_candidate(case: ReconciliationCase) -> int | None:
    """The case's one candidate entry: `case.entry_id`, else the entry of its only candidate (None for a group)."""
    if case.entry_id is not None:
        return case.entry_id
    candidates = case.candidates or []
    if len(candidates) == 1 and candidates[0].get("entry_id") is not None:
        return int(candidates[0]["entry_id"])
    return None


def adjustable(db: Session, case: ReconciliationCase) -> bool:
    """Whether `adjust_amount` would accept the case's single candidate: standalone (no parent, no group, no posted
    schedule instance, the checks `ledger_actions` makes) and not protected."""
    entry_id = _single_candidate(case)
    entry = None if entry_id is None else db.get(LedgerEntry, entry_id)
    if entry is None or entry.parent_entry_id is not None or entry.group_id is not None:
        return False
    if any(base.schedule_rows(db, [entry.id])):
        return False
    return split_protection.protected_reason(db, entry) is None


def default_category_id(db: Session, *, account_id: int, line_kind: str, merchant_pattern: str,
                        flow_sign: str) -> int | None:
    """The record sheet's default category (spec §4.3/§7.2): the newest `record` owner rule of this shape (enabled or
    not), else the newest categorised entry of the account whose merchant or name contains the pattern."""
    params = db.execute(
        select(ReconciliationOwnerRule.params)
        .where(ReconciliationOwnerRule.account_id == account_id, ReconciliationOwnerRule.line_kind == line_kind,
               ReconciliationOwnerRule.merchant_pattern == merchant_pattern,
               ReconciliationOwnerRule.flow_sign == flow_sign, ReconciliationOwnerRule.action == "record")
        .order_by(ReconciliationOwnerRule.created_at.desc(), ReconciliationOwnerRule.id.desc()).limit(1)
    ).scalar_one_or_none()
    if params and params.get("category_id") is not None:
        return int(params["category_id"])
    if not merchant_pattern:  # an empty pattern would match every entry
        return None
    return db.execute(
        select(LedgerEntry.category_id)
        .where(LedgerEntry.account_id == account_id, LedgerEntry.category_id.is_not(None),
               or_(LedgerEntry.merchant.icontains(merchant_pattern, autoescape=True),
                   LedgerEntry.name.icontains(merchant_pattern, autoescape=True)))
        .order_by(LedgerEntry.posted_date.desc(), LedgerEntry.id.desc()).limit(1)
    ).scalar_one_or_none()


def _by_id(db: Session, model, ids) -> dict:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {row.id: row for row in db.execute(select(model).where(model.id.in_(ids))).scalars()}


def _title(entry: LedgerEntry, categories: dict[int, Category]) -> str:
    category = categories.get(entry.category_id) if entry.category_id is not None else None
    return entry.name or entry.merchant or (category.name if category is not None else None) or entry.kind


def _candidate(entry: LedgerEntry, categories: dict[int, Category], *, reason: str | None, residual,
               date_distance) -> dict:
    return {"entry_id": entry.id, "date": entry.posted_date, "title": _title(entry, categories),
            "amount": entry.amount, "reason": reason, "residual": None if residual is None else str(residual),
            "date_distance": None if date_distance is None else int(date_distance)}


def _gate(statement_kind: str, line_kind: str, sign: str):
    """The matcher's entry-class gate as SQL: a settlement entry counts as settlement_in/out (by its sign, which the
    caller already fixed to the line's), every other entry by its kind."""
    gate = matching.eligible_kinds(statement_kind, line_kind)
    settlement = ("settlement_out" if sign == "-" else "settlement_in") in gate
    kinds = [k for k in gate if not k.startswith("settlement_")]
    return or_(and_(LedgerEntry.is_settlement.is_(False), LedgerEntry.kind.in_(kinds)) if kinds else false(),
               LedgerEntry.is_settlement.is_(True) if settlement else false())


def search_candidates(db: Session, case_id: int, *, days: int = 14, limit: int = 50) -> list[dict]:
    """Entries the owner may match the case's line to beyond its listed candidates (spec §4.3): the statement
    account's top-level entries within ±`days`, same sign, gated like the matcher, unclaimed; newest first."""
    case = db.get(ReconciliationCase, case_id)
    if case is None:
        raise NotFoundError(f"case {case_id} not found")
    statement = db.get(AccountStatement, case.statement_id)
    line = base.current_line(db, case, statement)
    if line is None:
        return []
    sign = flow_sign(line.flow_amount)
    listed = {c.get("entry_id") for c in case.candidates or []} | {case.entry_id}
    excluded = (listed - {None}) | coverage_service.claimed_entry_ids(db)
    q = (select(LedgerEntry)
         .where(LedgerEntry.account_id == statement.account_id, LedgerEntry.parent_entry_id.is_(None),
                LedgerEntry.posted_date.between(line.posted_date - timedelta(days=days),
                                                line.posted_date + timedelta(days=days)),
                LedgerEntry.amount < 0 if sign == "-" else LedgerEntry.amount >= 0,
                _gate(statement.kind, line.line_kind, sign))
         .order_by(LedgerEntry.posted_date.desc(), LedgerEntry.id.desc()))
    if excluded:
        q = q.where(LedgerEntry.id.not_in(excluded))
    entries = list(db.execute(q.limit(limit)).scalars())
    categories = _by_id(db, Category, (e.category_id for e in entries))
    return [_candidate(e, categories, reason="search", residual=None,
                       date_distance=abs((e.posted_date - line.posted_date).days)) for e in entries]


def _line_out(line: StatementLine | None) -> dict | None:
    if line is None:
        return None
    return {"id": line.id, "date": line.posted_date, "merchant": line.merchant_norm, "amount": line.flow_amount,
            "line_kind": str(line.line_kind)}


def _entry_out(entry: LedgerEntry | None, categories: dict[int, Category]) -> dict | None:
    if entry is None:
        return None
    return {"id": entry.id, "date": entry.posted_date, "title": _title(entry, categories), "amount": entry.amount,
            "account_id": entry.account_id}


def _listed_candidates(case: ReconciliationCase, entries: dict[int, LedgerEntry],
                       categories: dict[int, Category]) -> list[dict]:
    """The case's stored entry candidates, resolved, in stored order (group candidates skipped: `match` from the
    inbox names an entry)."""
    out = []
    for cand in case.candidates or []:
        entry = entries.get(cand.get("entry_id"))
        if entry is None:
            continue
        out.append(_candidate(entry, categories, reason=cand.get("reason"), residual=cand.get("residual"),
                              date_distance=cand.get("date_distance")))
        if len(out) == CANDIDATE_LIMIT:
            break
    return out


def _proposal_out(db: Session, case: ReconciliationCase) -> dict | None:
    if case.status != "proposed":
        return None
    pending = proposal_service.pending_for_case(db, case.id)
    if not pending:
        return None
    p = pending[0]
    return {"id": p.id, "action": p.action, "params": p.params, "author": p.author, "rationale": p.rationale}


def _rule_hint(db: Session, statement: AccountStatement, line: StatementLine, cache: dict) -> dict:
    merchant_pattern = pattern(line.merchant_norm or "")
    sign = flow_sign(line.flow_amount)
    key = (statement.account_id, str(line.line_kind), merchant_pattern, sign)
    if key not in cache:
        cache[key] = default_category_id(db, account_id=statement.account_id, line_kind=str(line.line_kind),
                                         merchant_pattern=merchant_pattern, flow_sign=sign)
    return {"merchant_pattern": merchant_pattern, "line_kind": str(line.line_kind), "flow_sign": sign,
            "amount": f"{line.flow_amount:.4f}", "default_category_id": cache[key]}


def _item(db: Session, case: ReconciliationCase, statement: AccountStatement, line: StatementLine | None,
          entries: dict[int, LedgerEntry], categories: dict[int, Category], hint_cache: dict) -> dict:
    allowed = reconciliation_actions.allowed(case)
    question = classify(kind=case.kind, status=case.status, statement_kind=statement.kind,
                        hint=(case.context or {}).get("hint"),
                        merchant_norm=line.merchant_norm if line else None, allowed=allowed)
    # `adjustable` only matters for amount_differs (the one question offering accept_bank_amount)
    actions = buttons(question, allowed, adjustable=question == "amount_differs" and adjustable(db, case))
    learn = line is not None and any(learnable(question, b) for b in actions)
    return {
        "case_id": case.id, "case_version": case.version, "kind": case.kind, "status": case.status,
        "question": question, "folded": question in FOLDED,
        "group_key": None if line is None else group_key(question, statement.account_id, str(line.line_kind),
                                                          line.merchant_norm or "", line.flow_amount),
        "statement": {"id": statement.id, "period_end": statement.period_end},
        "line": _line_out(line),
        "entry": _entry_out(entries.get(case.entry_id), categories),
        "candidates": _listed_candidates(case, entries, categories),
        "proposal": _proposal_out(db, case),
        "actions": actions,
        "rule_hint": _rule_hint(db, statement, line, hint_cache) if learn else None,
    }


def _counts(items: list[dict]) -> dict:
    """Over the whole filtered set: `ask` = unfolded cards (one per group_key, one per keyless item)."""
    unfolded = [i for i in items if not i["folded"]]
    keys = {i["group_key"] for i in unfolded if i["group_key"] is not None}
    keyless = sum(1 for i in unfolded if i["group_key"] is None)
    counts = {"ask": len(keys) + keyless, "ask_items": len(unfolded)}
    for question in ("proposed", "balance_gap", "settlement", "other"):
        counts[question] = sum(1 for i in items if i["question"] == question)
    return counts


def build_inbox(db: Session, *, statuses: tuple[str, ...] = ("open", "proposed"), account_id: int | None = None,
                limit: int = 500) -> dict:
    """The spec §4 `InboxOut` over the open/proposed cases of LIVE statements. Supersedes stale proposals first
    (the caller commits, like `get_case`)."""
    proposal_service.supersede_stale(db)
    q = (select(ReconciliationCase, AccountStatement, Account)
         .join(AccountStatement, AccountStatement.id == ReconciliationCase.statement_id)
         .join(Account, Account.id == AccountStatement.account_id)
         .where(ReconciliationCase.status.in_(statuses), AccountStatement.mode == "live"))
    if account_id is not None:
        q = q.where(AccountStatement.account_id == account_id)
    rows = db.execute(q).all()
    lines = _by_id(db, StatementLine, (case.line_id for case, _, _ in rows))
    entry_ids = {case.entry_id for case, _, _ in rows}
    entry_ids |= {c.get("entry_id") for case, _, _ in rows for c in case.candidates or []}
    entries = _by_id(db, LedgerEntry, entry_ids)
    categories = _by_id(db, Category, (e.category_id for e in entries.values()))
    hint_cache: dict = {}
    keyed = []
    for case, statement, account in rows:
        line = lines.get(case.line_id)
        item = _item(db, case, statement, line, entries, categories, hint_cache)
        entry = entries.get(case.entry_id)
        day = line.posted_date if line else (entry.posted_date if entry else date.max)
        keyed.append(((account.name, -statement.period_end.toordinal(), day, case.id), account, item))
    keyed.sort(key=lambda row: row[0])
    counts = _counts([item for _, _, item in keyed])
    accounts: list[dict] = []
    for _, account, item in keyed[:limit]:
        if not accounts or accounts[-1]["id"] != account.id:
            accounts.append({"id": account.id, "name": account.name, "currency": account.currency,
                             "is_credit": bool(account.is_credit), "items": []})
        accounts[-1]["items"].append(item)
    return {"generated_at": datetime.now(timezone.utc), "counts": counts, "accounts": accounts}
