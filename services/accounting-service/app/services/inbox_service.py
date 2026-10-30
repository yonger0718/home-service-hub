"""Reconciliation inbox: question classifier, group key and button rules.

See docs/superpowers/specs/2026-10-21-reconciliation-inbox-design.md (sections 4.1-4.3, 5).
The functions here are pure; the DB read model and the batch apply live further down this module.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Iterable

from sqlalchemy import and_, false, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Account, AccountStatement, Category, EntryGroup, LedgerEntry, ReconciliationAudit, ReconciliationCase,
    ReconciliationOwnerRule, StatementLine,
)
from app.schemas.inbox import InboxApplyIn, InboxCaseRef
from app.services import (
    coverage_service, proposal_service, reconciliation_actions, settings_service, split_protection,
)
from app.services import entry_write_service as ews
from app.services.edit_lock import EditLockedError
from app.services.errors import (
    CodedConflictError, CodedValidationError, ConflictError, NotFoundError, ValidationError,
)
from app.services.reconciliation_actions import OPEN_STATUSES, audit, base, engine
from app.services.reconciliation_actions.base import Context
from app.services.statements import matching

SETTLEMENT_LABELS: tuple[str, ...] = matching.SETTLEMENT_LABELS  # the default; callers pass the rules' labels
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
             merchant_norm: str | None, allowed: list[str],
             settlement_labels: tuple[str, ...] = matching.SETTLEMENT_LABELS) -> str:
    """Map a reconciliation case to its inbox question. Portfolio integration spec §8: a `settlement` case (a
    booking candidate or `source_drift`) and an `ambiguous` one with hint `settlement_ambiguous` are `settlement`
    questions, as an unmatched bank line naming a settlement label (`settlement_labels`, the rules' list) is; a
    `dividend` case is `other` until its follow-up plan gives it a card."""
    if (statement_kind == "bank" and kind == "line_unmatched"
            and any(label in (merchant_norm or "") for label in settlement_labels)):
        return "settlement"
    if kind == "settlement" or (kind == "ambiguous" and hint == "settlement_ambiguous"):
        return "settlement"
    if kind == "dividend":
        return "other"
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
    # a settlement candidate names a fingerprint, not an entry: skipped, never an error
    if len(candidates) == 1 and isinstance(candidates[0].get("entry_id"), int):
        return candidates[0]["entry_id"]
    return None


def adjustable(db: Session, case: ReconciliationCase, *, check_lock: bool = True) -> bool:
    """Whether `adjust_amount` would accept the case's single candidate: standalone or a split member (no parent, no
    group other than a `split`, no posted schedule instance, the checks `ledger_actions` makes) and not protected.
    `check_lock=False` leaves the cutover lock to the engine (the rule runner audits it as `locked_until_cutover`)."""
    entry_id = _single_candidate(case)
    entry = None if entry_id is None else db.get(LedgerEntry, entry_id)
    if entry is None or entry.parent_entry_id is not None:
        return False
    if entry.group_id is not None and db.get(EntryGroup, entry.group_id).kind != "split":
        return False
    if any(base.schedule_rows(db, [entry.id])):
        return False
    if check_lock:
        try:
            ews.assert_entry_editable(db, entry)
        except EditLockedError:
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
            "date_distance": None if date_distance is None else int(date_distance), "group_id": entry.group_id}


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
    account's top-level entries outside any group or in a `split` group within ±`days`, same sign, gated like the
    matcher, unclaimed; newest first. Listing a split member is an owner override (2026-10-24 spec §5.3): it may be
    a member of a group the statement prints whole — the `match` that follows requires conservation."""
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
         .outerjoin(EntryGroup, EntryGroup.id == LedgerEntry.group_id)
         .where(LedgerEntry.account_id == statement.account_id, LedgerEntry.currency == statement.currency,
                LedgerEntry.parent_entry_id.is_(None),
                or_(LedgerEntry.group_id.is_(None), EntryGroup.kind == "split"),  # match refuses other members
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
        entry_id = cand.get("entry_id")
        entry = entries.get(entry_id) if isinstance(entry_id, int) else None  # a settlement candidate has none
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


def settlement_labels(db: Session) -> tuple[str, ...]:
    """The rules' `settlement_labels` (the one setting the matcher's settlement-line test reads too)."""
    return tuple(settings_service.reconciliation_rules(db).get("settlement_labels") or matching.SETTLEMENT_LABELS)


def _question(case: ReconciliationCase, statement: AccountStatement, line: StatementLine | None,
              allowed: list[str], *, labels: tuple[str, ...]) -> str:
    return classify(kind=case.kind, status=case.status, statement_kind=statement.kind,
                    hint=(case.context or {}).get("hint"),
                    merchant_norm=line.merchant_norm if line else None, allowed=allowed, settlement_labels=labels)


def _buttons(db: Session, case: ReconciliationCase, question: str, allowed: list[str]) -> list[str]:
    # `adjustable` only matters for amount_differs (the one question offering accept_bank_amount)
    return buttons(question, allowed, adjustable=question == "amount_differs" and adjustable(db, case))


def _transfer_out(db: Session, case: ReconciliationCase, allowed: list[str]) -> dict | None:
    """Spec §7: the parameters of the transfer actions, which have no inbox button (`actions` is unchanged)."""
    adjust = pair = None
    if "adjust_transfer" in allowed:
        if case.kind == "ambiguous":
            entry_id = None
            legs = [c["entry_id"] for c in case.candidates or ()
                    if reconciliation_actions._transfer_leg(case, c.get("entry_id"))]
        else:
            entry_id = (case.context or {}).get("entry_id") or case.entry_id
            legs = [entry_id]
        adjust = {"entry_id": entry_id, "line_id": case.line_id, "candidate_entry_ids": legs}
    if "pair_transfer" in allowed:
        action = reconciliation_actions.ACTIONS["pair_transfer"]
        names = {}
        candidates = []
        for peer_statement, peer_line in action.peer_candidates(db, case):
            if peer_statement.account_id not in names:
                names[peer_statement.account_id] = db.get(Account, peer_statement.account_id).name
            candidates.append({"line_id": peer_line.id, "statement_id": peer_statement.id,
                               "account_name": names[peer_statement.account_id],
                               "posted_date": peer_line.posted_date, "amount": peer_line.flow_amount})
        pair = {"peer_candidates": candidates}
    return None if adjust is None and pair is None else {"adjust_transfer": adjust, "pair_transfer": pair}


def _item(db: Session, case: ReconciliationCase, statement: AccountStatement, line: StatementLine | None,
          entries: dict[int, LedgerEntry], categories: dict[int, Category], hint_cache: dict,
          labels: tuple[str, ...]) -> dict:
    allowed = reconciliation_actions.allowed(case)
    question = _question(case, statement, line, allowed, labels=labels)
    actions = _buttons(db, case, question, allowed)
    learn = (line is not None and bool(pattern(line.merchant_norm or ""))  # an empty pattern can never be a rule
             and any(learnable(question, b) for b in actions))
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
        "transfer": _transfer_out(db, case, allowed),
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
    labels = settlement_labels(db)
    keyed = []
    for case, statement, account in rows:
        line = lines.get(case.line_id)
        item = _item(db, case, statement, line, entries, categories, hint_cache, labels)
        entry = entries.get(case.entry_id)
        day = line.posted_date if line else (entry.posted_date if entry else date.max)
        keyed.append(((account.name, account.id, -statement.period_end.toordinal(), day, case.id), account, item))
    keyed.sort(key=lambda row: row[0])
    counts = _counts([item for _, _, item in keyed])
    accounts: list[dict] = []
    for _, account, item in keyed[:limit]:
        if not accounts or accounts[-1]["id"] != account.id:
            accounts.append({"id": account.id, "name": account.name, "currency": account.currency,
                             "is_credit": bool(account.is_credit), "items": []})
        accounts[-1]["items"].append(item)
    return {"generated_at": datetime.now(timezone.utc), "counts": counts, "accounts": accounts}


# --- batch apply (spec §5) --------------------------------------------------------------------------------------

REFUSALS = (ValidationError, ConflictError, NotFoundError, EditLockedError)


def translate(button: str, params: dict, case: ReconciliationCase, line: StatementLine | None,
              answer: str) -> tuple[str, dict]:
    """The registry action and params behind an inbox button (spec §4.3); a missing param is a 422."""
    def need(name: str):
        if params.get(name) is None:
            raise ValidationError("params", f"{name} required")
        return params[name]

    action = BUTTONS_TO_ACTIONS[button]
    if button == "record":
        if line is None:
            raise ValidationError("params", "line required")
        out = {"kind": "expense" if line.flow_amount < 0 else "income", "category_id": need("category_id"),
               "name": params.get("name"), "project_id": params.get("project_id")}
        return action, {k: v for k, v in out.items() if v is not None}
    if button == "match_candidate":
        if "entry_ids" not in params:
            return action, {"line_id": case.line_id, "entry_id": need("entry_id")}
        per_case = params["entry_ids"] if isinstance(params["entry_ids"], dict) else {}
        if per_case.get(str(case.id)) is None:
            raise ValidationError("params", f"entry_ids.{case.id} required")
        return action, {"line_id": case.line_id, "entry_id": per_case[str(case.id)]}
    if button == "accept_bank_amount":
        entry_id = _single_candidate(case)
        if entry_id is None:
            raise ValidationError("params", "entry_id required")
        return action, {"entry_id": entry_id, "line_id": case.line_id}
    if button == "defer":
        if case.entry_id is None:
            raise ValidationError("params", "entry_id required")
        return action, {"entry_id": case.entry_id}
    if button == "dismiss":
        return action, {"note": f"inbox: {answer}"}
    return action, {}  # anchor


def expect_version(expected: int) -> Callable[[Context], None]:
    """An engine precheck refusing `stale_case` when the locked case is no longer at the version the card showed."""
    def check(ctx: Context) -> None:
        if ctx.case.version != expected:
            raise CodedConflictError("stale_case", f"case {ctx.case.id} is at version {ctx.case.version}")
    return check


def _result(case_id: int, status: str, action_id: int | None = None, code: str | None = None) -> dict:
    return {"case_id": case_id, "status": status, "action_id": action_id, "code": code}


def _refusal_code(exc: Exception) -> str:
    """The per-member refusal code: a coded error's own code, else a stable name for its class."""
    if getattr(exc, "code", None):
        return exc.code
    if isinstance(exc, EditLockedError):
        return "locked_until_cutover"
    if isinstance(exc, NotFoundError):
        return "not_found"
    if isinstance(exc, ConflictError):
        return "conflict"
    return exc.field  # a plain ValidationError names its field


def _check_params(body: InboxApplyIn) -> None:
    """The params the button cannot do without (422, nothing applied): `record` needs `category_id`;
    `match_candidate` needs `entry_ids` naming every member's case when it sends `entry_ids` at all (`translate`
    then reads only it), else `entry_id` for a single member."""
    params = body.params
    if body.action == "record" and params.get("category_id") is None:
        raise ValidationError("params", "category_id required")
    if body.action == "match_candidate":
        if "entry_ids" in params:
            per_case = params["entry_ids"]
            ok = isinstance(per_case, dict) and all(per_case.get(str(ref.case_id)) is not None for ref in body.cases)
        else:
            ok = len(body.cases) == 1 and params.get("entry_id") is not None
        if not ok:
            raise ValidationError("params", "entry_id required" if len(body.cases) == 1
                                  else "entry_ids required for every case")


def _asked_question(case: ReconciliationCase, statement: AccountStatement, line: StatementLine | None, *,
                    labels: tuple[str, ...]) -> str:
    """The question the case's card asked: as it stands while open, else as if still open with the whole registry
    (a replayed member is closed now; `allowed` only narrows `create_entry`, which `needs_peer` already decides)."""
    if case.status in OPEN_STATUSES:
        return _question(case, statement, line, reconciliation_actions.allowed(case), labels=labels)
    return classify(kind=case.kind, status="open", statement_kind=statement.kind,
                    hint=(case.context or {}).get("hint"), merchant_norm=line.merchant_norm if line else None,
                    allowed=list(reconciliation_actions.ACTIONS), settlement_labels=labels)


def _check_batch(db: Session, body: InboxApplyIn) -> tuple[list[ReconciliationCase], list[StatementLine | None]]:
    """Structural checks before anything applies: no duplicate member, every case exists, the params the button
    needs, and the members still open at the card's version share one question that offers the button (closed or
    newer members are judged per member). Returns the cases and their current lines in request order."""
    ids = [ref.case_id for ref in body.cases]
    if len(set(ids)) != len(ids):
        raise ValidationError("cases", "duplicate case")
    by_id = _by_id(db, ReconciliationCase, ids)
    missing = [case_id for case_id in ids if case_id not in by_id]
    if missing:
        raise NotFoundError(f"case {missing[0]} not found")
    _check_params(body)
    statements = _by_id(db, AccountStatement, (case.statement_id for case in by_id.values()))
    cases = [by_id[case_id] for case_id in ids]
    lines = [base.current_line(db, case, statements[case.statement_id]) for case in cases]
    labels = settlement_labels(db)
    questions = set()
    for ref, case, line in zip(body.cases, cases, lines):
        if case.status in OPEN_STATUSES and case.version == ref.case_version:
            questions.add(_question(case, statements[case.statement_id], line, reconciliation_actions.allowed(case),
                                    labels=labels))
    if len(questions) > 1:
        raise ValidationError("cases", "mixed questions")
    if questions:
        (question,) = questions
        if body.action not in buttons(question, list(reconciliation_actions.ACTIONS), adjustable=True):
            raise ValidationError("action", f"not offered for {question}")
    return cases, lines


def _rule_key(db: Session, body: InboxApplyIn, cases: list[ReconciliationCase],
              lines: list[StatementLine | None]):
    """Step 2 (spec §6.2), before any member applies, over the members the card judged — open at the card's version,
    or closed (a replay; anything else is refused at step 3 and learning is skipped): the answer must be learnable
    for every judged member's question and every one must give the same rule key (422 otherwise). Returns
    (question, key, rule_error): no key when no member is judged; `rule_error` `no_line` (learning skipped, the
    members still apply) when a judged member's line is no longer printed."""
    from app.services import owner_rule_service  # local: owner_rule_service imports this module

    judged = [(case, line) for ref, case, line in zip(body.cases, cases, lines)
              if case.status not in OPEN_STATUSES or case.version == ref.case_version]
    if not judged:
        return None, None, None
    labels = settlement_labels(db)
    questions = [_asked_question(case, db.get(AccountStatement, case.statement_id), line, labels=labels)
                 for case, line in judged]
    refused = next((q for q in questions if not learnable(q, body.action)), None)
    if refused is not None:
        raise CodedValidationError("learn", "not_learnable", f"{body.action} on {refused}")
    if any(line is None for _, line in judged):
        return None, None, "no_line"
    key = owner_rule_service.rule_key_for(db, [c for c, _ in judged], [line for _, line in judged],
                                          amount_exact=body.learn.amount_exact, button=body.action)
    return questions[0], key, None


def _apply_member(db: Session, ref: InboxCaseRef, body: InboxApplyIn, *, label: str,
                  request_id: str | None) -> dict:
    """One member in its own transaction: a replay of the same tap, else the button as the case offers it now,
    applied through the engine at the card's version; any refusal rolls back this member only."""
    try:
        case = db.get(ReconciliationCase, ref.case_id, populate_existing=True)
        if case is None:
            raise NotFoundError(f"case {ref.case_id} not found")
        statement = db.get(AccountStatement, case.statement_id)
        line = base.current_line(db, case, statement)
        try:
            action, params = translate(body.action, body.params, case, line, body.answer)
        except ValidationError:  # no line / candidate / entry for the button now: refused below, after step 3b
            action = params = None
        if action is not None:
            found = engine.find_by_key(db, engine.key_for(case.id, ref.case_version, action, params))
            if found is not None and found.status == "applied":  # a retried tap: no re-apply
                audit.record(db, event="action_replayed", actor=label, statement_id=case.statement_id,
                             case_id=case.id, action_id=found.id, request_id=request_id,
                             detail={"action": found.action})
                db.commit()
                return _result(case.id, "replayed", found.id)
        if case.status not in OPEN_STATUSES:
            return _result(case.id, "refused", code="case_closed")
        if case.version != ref.case_version:
            return _result(case.id, "refused", code="stale_case")
        allowed = reconciliation_actions.allowed(case)
        question = _question(case, statement, line, allowed, labels=settlement_labels(db))
        if action is None or body.action not in _buttons(db, case, question, allowed):
            return _result(case.id, "refused", code="not_offered")
        row, replayed = engine.apply_action(db, case_id=case.id, action=action, params=params, actor="owner",
                                            label=label, request_id=request_id,
                                            precheck=expect_version(ref.case_version))
        if not replayed:  # a concurrent identical tap wrote its own answer
            audit.record(db, event="inbox_answer", actor=label, statement_id=case.statement_id, case_id=case.id,
                         action_id=row.id, request_id=request_id,
                         detail={"answer": body.answer, "button": body.action})
        db.commit()
        return _result(case.id, "replayed" if replayed else "applied", row.id)
    except REFUSALS as exc:
        db.rollback()
        return _result(ref.case_id, "refused", code=_refusal_code(exc))


def apply_batch(db: Session, *, body: InboxApplyIn, label: str, request_id: str | None) -> dict:
    """`POST /reconciliation/inbox/apply` (spec §5, §6.2): (1) the structural checks and (2) with `learn`, the rule
    key — a 422/404 there applies nothing; (3) every member in request order, each committed or refused on its own;
    (4) with `learn` and no member refused, the rule is learned and committed. A failure at step 4 leaves the
    committed actions standing: `rule_id` None and `rule_error` the code (HTTP 200). A resend whose every member
    replays learns nothing (a delayed retry must not re-enable or re-create a rule the owner since disabled or
    deleted)."""
    from app.services import owner_rule_service  # local: owner_rule_service imports this module

    cases, lines = _check_batch(db, body)
    question, key, rule_error = (_rule_key(db, body, cases, lines) if body.learn is not None
                                 else (None, None, None))
    results = [_apply_member(db, ref, body, label=label, request_id=request_id) for ref in body.cases]
    rule_id = None
    statuses = [r["status"] for r in results]
    if key is not None and all(s in ("applied", "replayed") for s in statuses) and "applied" in statuses:
        try:
            rule_id = owner_rule_service.learn(db, question=question, button=body.action, key=key,
                                               params=body.params, created_from_case_id=cases[0].id).id
            db.commit()
        except (ValidationError, IntegrityError) as exc:
            db.rollback()
            rule_id, rule_error = None, getattr(exc, "code", None) or type(exc).__name__
    return {"results": results, "rule_id": rule_id, "rule_error": rule_error}


def action_labels(db: Session, action_ids: Iterable[int]) -> dict[int, tuple[str | None, str | None]]:
    """Per action: (engine label from the action_applied/action_replayed audit actor, inbox answer text)."""
    ids = list(action_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(ReconciliationAudit.action_id, ReconciliationAudit.event, ReconciliationAudit.actor,
               ReconciliationAudit.detail)
        .where(ReconciliationAudit.action_id.in_(ids),
               ReconciliationAudit.event.in_(("action_applied", "action_replayed", "inbox_answer")))
        .order_by(ReconciliationAudit.id)).all()
    applied: dict[int, str] = {}
    replayed: dict[int, str] = {}
    answers: dict[int, str] = {}
    for action_id, event, actor, detail in rows:
        if event == "action_applied":
            applied.setdefault(action_id, actor)
        elif event == "action_replayed":
            replayed.setdefault(action_id, actor)
        elif isinstance(detail, dict) and detail.get("answer") is not None:
            answers[action_id] = detail["answer"]
    return {i: (applied.get(i, replayed.get(i)), answers.get(i)) for i in ids}


def case_lines(db: Session, case_ids: Iterable[int]) -> dict[int, dict | None]:
    """Per case: `{date, merchant, amount}` of its current line (`base.current_line`'s rule — `case.line_id` when it
    is on the statement's current revision, else the event's line there), None without one. Batched: one query for
    the cases with their statements, one for the candidate lines."""
    ids = {i for i in case_ids if i is not None}
    if not ids:
        return {}
    pairs = db.execute(select(ReconciliationCase, AccountStatement.current_revision_id)
                       .join(AccountStatement, AccountStatement.id == ReconciliationCase.statement_id)
                       .where(ReconciliationCase.id.in_(ids))).all()
    line_ids = {case.line_id for case, _ in pairs if case.line_id is not None}
    revisions = {rev for case, rev in pairs if case.line_id is not None and case.event_id is not None}
    events = {case.event_id for case, _ in pairs if case.line_id is not None and case.event_id is not None}
    by_id: dict[int, StatementLine] = {}
    by_event: dict[tuple[int, int], StatementLine] = {}
    if line_ids:
        cond = StatementLine.id.in_(line_ids)
        if events:
            cond = or_(cond, and_(StatementLine.revision_id.in_(revisions), StatementLine.event_id.in_(events)))
        for line in db.execute(select(StatementLine).where(cond)).scalars():
            if line.id in line_ids:
                by_id[line.id] = line
            by_event.setdefault((line.revision_id, line.event_id), line)
    out: dict[int, dict | None] = {}
    for case, revision in pairs:
        line = None
        if case.line_id is not None:
            line = by_id.get(case.line_id)
            if line is None or line.revision_id != revision:
                line = by_event.get((revision, case.event_id)) if case.event_id is not None else None
        out[case.id] = (None if line is None
                        else {"date": line.posted_date, "merchant": line.merchant_norm, "amount": line.flow_amount})
    return out
