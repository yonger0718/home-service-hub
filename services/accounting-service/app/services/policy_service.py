"""The reconciliation policy engine (R1c Task 10; spec §7.3; rulings 6, 10, 22, 31): shipped off.

After a matching transaction committed, `run_policy` looks at one statement's open cases and lets the policy apply
the two money actions it may take on its own — `create_system_entry` for a bank-only line, `attach_fee` for an
`amount_delta` whose residual is the expected FX fee — plus, when `accept_agent_proposals` is on, an agent's pending
`match` proposal on an `ambiguous` case whose line has exactly one admissible representation left. Never anything
else (near deltas, balance gaps, conflicts, parse reviews, rechecks, historical statements).

Per candidate (ruling 22): an unlocked pre-filter (case still as listed, action enabled, `max_amount`, the
per-statement count/total) decides whether to try; then the engine runs with `budget=(month, currency, amount)` —
the `policy_budget` row is reserved before the statement lock (`BudgetExceeded` when the month's total would pass)
— and `_policy_precheck` under the statement and case locks re-validates everything and recounts the per-statement
caps from the applied `actor='policy'` rows (`CapExceeded`); only then is the budget row charged. Over a cap the
candidate becomes a proposal authored `policy` (an agent's own proposal just stays pending); an apply refused with a
422/409 is rolled back (its reservation with it) and audited `policy_skipped`; no `failed` action row is ever
written.

Commits (the one documented exception to "services never commit"): `run_policy` commits after each candidate's
apply, proposal or skip audit, so a later failure never loses an earlier reservation + effect pair; it is only
called by routers after their own commit (`run_policy_safe` keeps a policy error from failing the caller)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.models import (
    AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationAudit, ReconciliationCase,
    ReconciliationProposal, StatementLine,
)
from app.schemas.reconciliation import ProposalIn
from app.services import coverage_service, ledger_service, proposal_service, settings_service, stand_ins
from app.services import reconciliation_actions as registry
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, ConflictError, NotFoundError, ValidationError
from app.services.reconciliation_actions import OPEN_STATUSES, audit, engine
from app.services.reconciliation_actions.base import POLICY, Context, current_line
from app.services.reconciliation_actions.engine import BudgetExceeded
from app.services.statements import matching

__all__ = ["BudgetExceeded", "CapExceeded", "Candidate", "POLICY_ACTIONS", "PolicyResult", "apply_candidate",
           "collect_candidates", "run_policy", "run_policy_safe"]

log = logging.getLogger(__name__)

SYSTEM_LINE_KINDS = frozenset({"fee", "interest", "reward"})  # create_system_entry books these (no balance_adjustment)
EXCLUDING_CASES = ("parse_review", "statement_conflict")  # an open one keeps the policy off the whole statement
ACTION_DISABLED = "action_disabled"  # the normal state while shipped off: counted, never audited
CURRENCY = "currency"


class CapExceeded(CodedConflictError):
    """409 `cap_exceeded`; `reason` = `max_amount` | `per_statement_count` | `per_statement_total`."""

    def __init__(self, reason: str):
        super().__init__("cap_exceeded", reason)
        self.reason = reason


@dataclass(frozen=True)
class Candidate:
    """One thing the policy may do: `action` with `params` on case `case_id`, worth `amount` (≥ 0, display units)
    against the caps. `kind`/`status`/`entry_id` are the case as listed, re-checked under the lock; `proposal_id` is
    set for an agent proposal the policy would apply."""
    case_id: int
    kind: str
    status: str
    action: str
    params: dict
    amount: Decimal
    entry_id: int | None = None
    proposal_id: int | None = None
    evidence: dict = field(default_factory=dict)  # R6b.5 replace_stand_in: the case evidence `_unchanged` re-checks


@dataclass
class PolicyResult:
    applied: list[int] = field(default_factory=list)  # action ids
    proposed: list[int] = field(default_factory=list)  # proposal ids
    skipped: dict[str, int] = field(default_factory=dict)  # code → count


def _money(value) -> Decimal | None:
    """|value| as a plain Decimal (`Decimal('1200')`, not `1200.0000`), None when not a number."""
    try:
        amount = abs(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return Decimal(format(amount.normalize(), "f")) if amount.is_finite() else None


def _fee_now(db: Session, case: ReconciliationCase, line: StatementLine | None) -> Decimal | None:
    """R1d item 4: the fee `attach_fee` would book now — |entry.amount − line.flow_amount| from the rows as they are
    (the case's `context.residual` is the reconcile-time value and goes stale when the entry is edited before the
    next sweep). None without a line or an entry."""
    entry_id = (case.context or {}).get("entry_id")
    if line is None or not isinstance(entry_id, int) or isinstance(entry_id, bool):
        return None
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:
        return None
    return _money(Decimal(entry.amount) - Decimal(line.flow_amount))


# --- candidates ---------------------------------------------------------------------------------------------------

def _system_entry_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """An open `line_unmatched` with the bank-only hint → book the line as its own fee/interest/reward entry."""
    context = case.context or {}
    if case.kind != "line_unmatched" or context.get("hint") != "bank_only" or context.get("owner_deleted"):
        return None
    line = current_line(db, case, statement)
    if line is None or line.line_kind not in SYSTEM_LINE_KINDS:
        return None
    return Candidate(case.id, case.kind, case.status, "create_system_entry", {"kind": line.line_kind},
                     _money(line.flow_amount), entry_id=context.get("entry_id"))


def _attach_fee_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """An open `amount_delta` whose residual is the expected FX fee (`fee_matches`) → attach it to the entry."""
    context = case.context or {}
    entry_id = context.get("entry_id")
    if case.kind != "amount_delta" or context.get("fee_matches") is not True \
            or not isinstance(entry_id, int) or isinstance(entry_id, bool):
        return None
    amount = _fee_now(db, case, current_line(db, case, statement))
    if amount is None:
        return None
    return Candidate(case.id, case.kind, case.status, "attach_fee", {"entry_id": entry_id}, amount, entry_id=entry_id)


def _backfill_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """R6b.1 policy column: the owner eligibility plus live mode, the grace, the account list and no owner delete."""
    if case.kind != "line_unmatched" or (case.context or {}).get("owner_deleted"):
        return None
    kind = registry.backfill_eligibility(case)
    if kind is None or statement.mode != "live":
        return None
    policy = settings_service.reconciliation_policy(db)
    if statement.period_end + timedelta(days=int(policy["backfill_grace_days"])) > ledger_service._today():
        return None
    accounts = policy.get("backfill_accounts") or []
    if accounts and statement.account_id not in accounts:
        return None
    line = current_line(db, case, statement)
    if line is None:
        return None
    return Candidate(case.id, case.kind, case.status, "backfill_entry", {"kind": kind}, _money(line.flow_amount))


def _anchor_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """R6b.2: any open balance_gap of an un-anchored bank account; amount = |opening delta| at the target."""
    from app.services.reconciliation_actions import structural_actions as sa
    if case.kind != "balance_gap" or statement.kind != "bank" or sa.anchor_applied(db, statement.account_id):
        return None
    found = sa.anchor_delta(db, statement.account_id)
    if found is None or found[1] == 0:
        return None
    return Candidate(case.id, case.kind, case.status, "anchor_opening", {}, _money(abs(found[1])))


REPLACE_HINTS = ("shadowed_by_stand_in", "orphaned_stand_in")
REPLACE_AMOUNT = Decimal("0.0000")  # a replacement moves no money: charged as zero, counted toward the statement cap


def _replace_evidence(case: ReconciliationCase) -> dict:
    context = case.context or {}
    return {"hint": context.get("hint"), "backfill_action_id": context.get("backfill_action_id"),
            "entry_id": case.entry_id, "event_id": case.event_id}


def _replace_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """R6b.5: an open entry_unmatched case with a stand-in hint → replace the stand-in (amount 0)."""
    if case.kind != "entry_unmatched" or (case.context or {}).get("hint") not in REPLACE_HINTS:
        return None
    return Candidate(case.id, case.kind, case.status, "replace_stand_in", {}, REPLACE_AMOUNT, entry_id=None,
                     evidence=_replace_evidence(case))


POLICY_ACTIONS = {"create_system_entry": _system_entry_candidate, "attach_fee": _attach_fee_candidate,
                  "backfill_entry": _backfill_candidate, "anchor_opening": _anchor_candidate,
                  "replace_stand_in": _replace_candidate}


def _agent_authors(authors: Iterable[str]) -> set[str]:
    """R1d item 7: an agent is a propose-only token — configured with `propose` and neither `write` nor `legacy`
    (an owner token's proposal is the owner's own draft, never auto-applied). With scopes unconfigured
    (compatibility mode) every author but the policy counts, as before."""
    candidates = {author for author in authors if author != POLICY}
    if not auth.scopes_configured():
        return candidates
    scopes = auth.configured_scopes()
    return {author for author in candidates
            if "propose" in scopes.get(author, frozenset()) and not ({"write", "legacy"} & scopes.get(author, frozenset()))}


def _agent_candidates(db: Session, statement: AccountStatement) -> list[Candidate]:
    """Pending `match` proposals by an agent — a propose-only token, `_agent_authors` — on `ambiguous` cases of the
    statement, written at the case's current version."""
    rows = db.execute(
        select(ReconciliationProposal, ReconciliationCase)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationProposal.case_id)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind == "ambiguous",
               ReconciliationCase.status == "proposed", ReconciliationProposal.status == "pending",
               ReconciliationProposal.action == "match", ReconciliationProposal.author != POLICY,
               ReconciliationProposal.case_version == ReconciliationCase.version)
        .order_by(ReconciliationProposal.id)).all()
    out = []
    agents = _agent_authors({proposal.author for proposal, _ in rows})
    for proposal, case in rows:
        if proposal.author not in agents:
            continue
        line = current_line(db, case, statement)
        if line is not None:
            out.append(Candidate(case.id, case.kind, case.status, "match", dict(proposal.params),
                                 _money(line.flow_amount), entry_id=(case.context or {}).get("entry_id"),
                                 proposal_id=proposal.id))
    return out


def collect_candidates(db: Session, statement: AccountStatement, policy: dict) -> list[Candidate]:
    """Every candidate of the statement, by case id (read only; no flag, cap or exclusion is applied here)."""
    cases = db.execute(select(ReconciliationCase).where(
        ReconciliationCase.statement_id == statement.id, ReconciliationCase.status == "open",
        ReconciliationCase.kind.in_(("line_unmatched", "amount_delta", "balance_gap", "entry_unmatched")))
        .order_by(ReconciliationCase.id)).scalars().all()
    out = [c for case in cases for make in POLICY_ACTIONS.values() if (c := make(db, case, statement)) is not None]
    if policy.get("accept_agent_proposals") is True:
        out += _agent_candidates(db, statement)
    return sorted(out, key=lambda c: (c.case_id, c.proposal_id or 0))


# --- checks -------------------------------------------------------------------------------------------------------

def _rule(policy: dict, action: str) -> dict:
    return (policy.get("actions") or {}).get(action) or {}


def _statement_excluded(db: Session, statement: AccountStatement, policy: dict) -> str | None:
    if statement.mode != "live":
        return "not_live"
    if statement.conflict_open:
        return "conflict_open"
    if statement.needs_recheck:
        return "needs_recheck"
    blocking = db.execute(select(ReconciliationCase.kind).where(
        ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind.in_(EXCLUDING_CASES),
        ReconciliationCase.status.in_(OPEN_STATUSES)).limit(1)).scalar_one_or_none()
    if blocking is not None:
        return blocking
    if statement.currency not in (policy.get("per_currency_enabled") or ()):
        return CURRENCY
    return None


def preview(db: Session, statement: AccountStatement, policy: dict, today: date | None = None) -> list[tuple[Candidate, str]]:
    """R6b.7: what run_policy's unlocked pass would decide per candidate, without applying anything."""
    out = []
    excluded = _statement_excluded(db, statement, policy)
    for candidate in collect_candidates(db, statement, policy):
        if policy.get("enabled") is not True or not _rule(policy, candidate.action).get("enabled"):
            out.append((candidate, "disabled"))
        elif excluded:
            out.append((candidate, f"excluded:{excluded}"))
        else:
            out.append((candidate, _decision_label(_prefilter(db, candidate, policy))))
    return out


def _decision_label(result: tuple) -> str:
    """_prefilter always returns (verb, code, reason): ("try", None, None) -> "apply"; ("propose", None, <cap reason>)
    -> "propose:<reason>"; ("skip", <code>, None) -> "skip:<code>" (exact tuples pinned by the unit test)."""
    verb, code, reason = result
    if verb == "try":
        return "apply"
    if verb == "propose":
        return f"propose:{reason}" if reason else "propose"
    return f"skip:{code}" if code else "skip"


def _applied_totals(db: Session, statement_id: int) -> tuple[int, Decimal]:
    """(count, total amount) of the applied `actor='policy'` rows on the statement's cases; the amount is what each
    row charged to the budget (`after.policy_budget.amount`)."""
    afters = db.execute(
        select(ReconciliationAction.after)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .where(ReconciliationCase.statement_id == statement_id, ReconciliationAction.actor == POLICY,
               ReconciliationAction.status == "applied", ReconciliationAction.action != "revert")).scalars().all()
    total = sum((_money(((after or {}).get("policy_budget") or {}).get("amount", "0")) or Decimal(0)
                 for after in afters), Decimal(0))
    return len(afters), total


def _cap_reason(db: Session, statement_id: int, candidate: Candidate, policy: dict) -> str | None:
    if candidate.amount > Decimal(str(_rule(policy, candidate.action).get("max_amount", "0"))):
        return "max_amount"
    count, total = _applied_totals(db, statement_id)
    if count + 1 > int(policy["per_statement_count"]):
        return "per_statement_count"
    if total + candidate.amount > Decimal(str(policy["per_statement_total"])):
        return "per_statement_total"
    return None


def _amount_now(ctx: Context, candidate: Candidate) -> Decimal | None:
    if candidate.action == "replace_stand_in":
        return REPLACE_AMOUNT
    if candidate.action == "attach_fee":
        return _fee_now(ctx.db, ctx.case, ctx.line)  # R1d item 4: recomputed under the lock, not context.residual
    if candidate.action == "anchor_opening":  # before the barrier: the action's own post-barrier check is the second line
        from app.services.reconciliation_actions import structural_actions as sa
        found = sa.anchor_delta(ctx.db, ctx.statement.account_id)
        return None if found is None else _money(abs(found[1]))
    return None if ctx.line is None else _money(ctx.line.flow_amount)


def _unchanged(ctx: Context, candidate: Candidate) -> bool:
    """The locked case is still the candidate: same status, kind, `context.entry_id`, amount and (for a system
    entry) the bank-only hint on a line of the proposed kind."""
    case, context = ctx.case, ctx.case.context or {}
    if (case.status, case.kind, context.get("entry_id")) != (candidate.status, candidate.kind, candidate.entry_id):
        return False
    if _amount_now(ctx, candidate) != candidate.amount:
        return False
    if candidate.action in ("create_system_entry", "backfill_entry") and (
            context.get("owner_deleted") or case.event_id in stand_ins.owner_deleted_events(ctx.db, ctx.statement)):
        return False  # re-derived from the action table under the lock: the sweep may have retired it since the collect
    if candidate.action == "anchor_opening":
        from app.services.reconciliation_actions import structural_actions as sa
        return not sa.anchor_applied(ctx.db, ctx.statement.account_id)
    if candidate.action == "replace_stand_in":  # the hint, the backfill action, the entry and the event as collected
        return _replace_evidence(case) == candidate.evidence
    if candidate.action == "create_system_entry":
        return context.get("hint") == "bank_only" and ctx.line.line_kind == candidate.params.get("kind")
    if candidate.action == "backfill_entry":
        return not context.get("owner_deleted") and not case.candidates \
            and registry.backfill_eligibility(case) == candidate.params.get("kind")
    return True


def _is_proposed(rep: matching.Representation, params: dict) -> bool:
    """The representation is the one the `match` params claim: the group (members only) or the entry alone / with
    its children."""
    head = rep.rows[0]
    if params.get("group_id") is not None:
        return head.group_id == params["group_id"] and rep.kind in ("group", "installment_group")
    wanted = "principal_children" if params.get("with_children") else "standalone"
    return head.group_id is None and head.entry_id == params.get("entry_id") and rep.kind == wanted


def _in_candidates(case: ReconciliationCase, params: dict) -> bool:
    key = "group_id" if params.get("group_id") is not None else "entry_id"
    return params.get(key) in {c.get(key) for c in case.candidates or [] if isinstance(c, dict)}


def _single_admissible(ctx: Context, candidate: Candidate) -> None:
    """Ruling 22's validator, read only: the matcher's representations of the line over the current population
    (every entry claimed anywhere left out, as `reconcile` builds it otherwise) must be exactly one, the proposed
    one, among the case's candidates."""
    db, statement, line = ctx.db, ctx.statement, ctx.line
    if line is None:
        raise CodedConflictError("not_single_admissible", "the line is no longer printed")
    accounts = coverage_service.participating_accounts(db, statement.account_id)
    include_reward = any(row.line_kind == "reward" for row in rec._current_lines(db, statement))
    entries, groups = rec.load_candidates(db, statement, accounts, window_days=matching.population_window_days(ctx.rules),
                                          include_reward=include_reward,
                                          exclude=coverage_service.claimed_entry_ids(db))
    reps = matching._representations(statement.kind, rec._as_line(line), [e for e in entries if e.claimable], groups,
                                     rec.plan_map(db, statement.account_id), set(), ctx.rules)
    if len(reps) != 1 or not _is_proposed(reps[0], candidate.params) or not _in_candidates(ctx.case, candidate.params):
        raise CodedConflictError("not_single_admissible", f"{len(reps)} admissible representation(s)")


def _policy_precheck(candidate: Candidate, ctx: Context) -> None:
    """Under the statement and case locks (ruling 22): the policy and the action still enabled, the statement still
    live and not excluded, the case still the candidate, the agent validator, then the per-statement caps recounted
    from the applied policy rows (`CapExceeded`). The monthly budget was checked by the reservation."""
    db = ctx.db
    policy = settings_service.reconciliation_policy(db)
    if policy.get("enabled") is not True:
        raise CodedConflictError("policy_disabled")
    if candidate.proposal_id is not None and policy.get("accept_agent_proposals") is not True:
        raise CodedConflictError("policy_disabled", "accept_agent_proposals")
    if _rule(policy, candidate.action).get("enabled") is not True:
        raise CodedConflictError(ACTION_DISABLED, candidate.action)
    excluded = _statement_excluded(db, ctx.statement, policy)
    if excluded is not None:
        raise CodedConflictError("statement_excluded", excluded)
    if not _unchanged(ctx, candidate):
        raise CodedConflictError("candidate_changed", f"case {ctx.case.id}")
    if candidate.proposal_id is not None:
        _single_admissible(ctx, candidate)
    reason = _cap_reason(db, ctx.statement.id, candidate, policy)
    if reason is not None:
        raise CapExceeded(reason)


def _prefilter(db: Session, candidate: Candidate, policy: dict) -> tuple[str, str | None, str | None]:
    """Unlocked: ("try", None, None), ("propose", None, cap reason) or ("skip", code, reason) — an agent's proposal
    over a cap is skipped (it stays pending for the owner)."""
    case = db.execute(select(ReconciliationCase).where(ReconciliationCase.id == candidate.case_id)
                      .execution_options(populate_existing=True)).scalar_one_or_none()
    if case is None or (case.status, case.kind) != (candidate.status, candidate.kind):
        return "skip", "candidate_changed", None
    if candidate.proposal_id is not None:
        proposal = db.get(ReconciliationProposal, candidate.proposal_id, populate_existing=True)
        if proposal is None or proposal.status != "pending" or proposal.case_version != case.version:
            return "skip", "proposal_not_pending", None
    if _rule(policy, candidate.action).get("enabled") is not True:
        return "skip", ACTION_DISABLED, None
    reason = _cap_reason(db, case.statement_id, candidate, policy)
    if reason is not None:
        return ("skip", "cap_exceeded", reason) if candidate.proposal_id is not None else ("propose", None, reason)
    return "try", None, None


# --- apply --------------------------------------------------------------------------------------------------------

def apply_candidate(db: Session, statement: AccountStatement, candidate: Candidate, *, month: date,
                    run_id: int | None = None) -> ReconciliationAction | None:
    """The engine with the policy's budget and precheck, then the `policy_applied` audit. Returns the action row, or
    None when the engine replayed an existing row (nothing charged). Raises what the engine raises (incl.
    `BudgetExceeded` / `CapExceeded`). Never commits: `run_policy` does, per candidate."""
    row, replayed = engine.apply_action(
        db, case_id=candidate.case_id, action=candidate.action, params=candidate.params, actor=POLICY, label=POLICY,
        proposal_id=candidate.proposal_id, run_id=run_id, budget=(month, statement.currency, candidate.amount),
        precheck=partial(_policy_precheck, candidate))
    if replayed:
        return None
    audit.record(db, event="policy_applied", actor=POLICY, statement_id=statement.id, case_id=candidate.case_id,
                 action_id=row.id, run_id=run_id,
                 detail={"action": candidate.action, "amount": str(candidate.amount), "month": month.isoformat(),
                         "proposal_id": candidate.proposal_id})
    return row


def _code(exc: Exception) -> str:
    return getattr(exc, "code", None) or (f"invalid_{exc.field}" if isinstance(exc, ValidationError)
                                          else exc.__class__.__name__)


def _skip(db: Session, statement_id: int, candidate: Candidate, code: str, result: PolicyResult, *,
          run_id: int | None, reason: str | None = None) -> None:
    result.skipped[code] = result.skipped.get(code, 0) + 1
    if code == ACTION_DISABLED:
        return
    # R1d item 5: a deterministic skip repeats on every run; audit it once per (case, version, code, reason).
    # Read-version -> check -> insert under the statement -> case locks (the engine's order: the audit row's FK takes
    # FOR KEY SHARE on the statement, which must already be held). This runs after the candidate's rollback in
    # its own transaction and commits below.
    try:
        rec._lock_statement(db, statement_id)
    except NotFoundError:
        db.rollback()
        return
    version = db.execute(select(ReconciliationCase.version).where(ReconciliationCase.id == candidate.case_id)
                         .with_for_update()).scalar_one_or_none()
    stored_version = ReconciliationAudit.detail["case_version"].astext
    already = db.execute(select(ReconciliationAudit.id).where(
        ReconciliationAudit.event == "policy_skipped", ReconciliationAudit.case_id == candidate.case_id,
        ReconciliationAudit.detail["code"].astext == code,
        stored_version.is_(None) if version is None else stored_version == str(version),
        func.coalesce(ReconciliationAudit.detail["reason"].astext, "") == (reason or "")).limit(1)).first()
    if already is None:
        audit.record(db, event="policy_skipped", actor=POLICY, statement_id=statement_id, case_id=candidate.case_id,
                     run_id=run_id, detail={"code": code, "action": candidate.action, "reason": reason,
                                            "proposal_id": candidate.proposal_id, "case_version": version})
    db.commit()


def _propose(db: Session, statement_id: int, candidate: Candidate, reason: str, result: PolicyResult, *,
             run_id: int | None) -> None:
    try:
        proposal = proposal_service.create_proposal(
            db, case_id=candidate.case_id, author=POLICY,
            payload=ProposalIn(action=candidate.action, params=candidate.params, rationale=f"policy: {reason}",
                               confidence=Decimal("1")))
        audit.record(db, event="policy_proposed", actor=POLICY, statement_id=statement_id, case_id=candidate.case_id,
                     run_id=run_id, detail={"proposal_id": proposal.id, "action": candidate.action, "reason": reason,
                                            "amount": str(candidate.amount)})
        db.commit()
    except (ValidationError, ConflictError, NotFoundError) as exc:
        db.rollback()
        _skip(db, statement_id, candidate, _code(exc), result, run_id=run_id, reason=reason)
        return
    result.proposed.append(proposal.id)


def _run_candidate(db: Session, statement_id: int, candidate: Candidate, result: PolicyResult, *, month: date,
                   run_id: int | None) -> None:
    """One candidate, in its own transaction (committed or rolled back here)."""
    policy = settings_service.reconciliation_policy(db)
    verdict, code, reason = _prefilter(db, candidate, policy)
    if verdict == "skip":
        _skip(db, statement_id, candidate, code, result, run_id=run_id, reason=reason)
        return
    if verdict == "propose":
        _propose(db, statement_id, candidate, reason, result, run_id=run_id)
        return
    statement = db.get(AccountStatement, statement_id)
    try:
        row = apply_candidate(db, statement, candidate, month=month, run_id=run_id)
    except (CapExceeded, BudgetExceeded) as exc:
        db.rollback()  # the attempt savepoint already undid the reservation; nothing of this candidate stays
        if candidate.proposal_id is not None:  # the agent's proposal stays pending for the owner
            _skip(db, statement_id, candidate, exc.code, result, run_id=run_id, reason=exc.reason)
        else:
            _propose(db, statement_id, candidate, exc.reason, result, run_id=run_id)
        return
    except (ValidationError, ConflictError, NotFoundError) as exc:
        db.rollback()
        code = _code(exc)
        if code == "proposal_superseded":  # persist what the engine found (its savepoint rolled the mark back)
            proposal_service.supersede_stale(db, case_id=candidate.case_id)
            db.commit()  # release its proposal/audit locks: _skip must take statement -> case first
        _skip(db, statement_id, candidate, code, result, run_id=run_id)
        return
    if row is None:
        db.rollback()
        _skip(db, statement_id, candidate, "replayed", result, run_id=run_id)
        return
    db.commit()
    result.applied.append(row.id)


def run_policy(db: Session, statement_id: int, *, run_id: int | None = None,
               today: date | None = None) -> PolicyResult:
    """Apply the policy to one statement (module docstring). Empty at once when the policy is off, the statement is
    not live, has `conflict_open`/`needs_recheck` or an open `parse_review`/`statement_conflict`; a currency outside
    `per_currency_enabled` is skipped with audit `policy_skipped currency`. The budget month is the application
    day's (`ledger_service._today()` unless `today`, ruling 10). Commits per candidate."""
    result = PolicyResult()
    policy = settings_service.reconciliation_policy(db)
    statement = db.get(AccountStatement, statement_id, populate_existing=True)
    if policy.get("enabled") is not True or statement is None:
        return result
    excluded = _statement_excluded(db, statement, policy)
    if excluded == CURRENCY:
        result.skipped[CURRENCY] = 1
        audit.record(db, event="policy_skipped", actor=POLICY, statement_id=statement_id, run_id=run_id,
                     detail={"code": CURRENCY, "currency": statement.currency})
        db.commit()
        return result
    if excluded is not None:
        return result
    month = (today or ledger_service._today()).replace(day=1)
    anchored: set[int] = set()  # one anchor per account per run
    for candidate in collect_candidates(db, statement, policy):
        if candidate.action == "anchor_opening":
            if statement.account_id in anchored:
                _skip(db, statement_id, candidate, "anchored_this_run", result, run_id=run_id)
                continue
            anchored.add(statement.account_id)
        _run_candidate(db, statement_id, candidate, result, month=month, run_id=run_id)
    return result


def run_policy_safe(db: Session, statement_id: int, *, run_id: int | None = None) -> PolicyResult:
    """For the routes, after their commit: any error rolls back and is only audited (`policy_skipped` with the
    exception class), never raised; the caller's own result stands."""
    try:
        return run_policy(db, statement_id, run_id=run_id)
    except Exception as exc:  # noqa: BLE001  (policy never fails the caller)
        log.exception("policy run failed for statement %s", statement_id)
        db.rollback()
        try:
            audit.record(db, event="policy_skipped", actor=POLICY, statement_id=statement_id, run_id=run_id,
                         detail={"code": exc.__class__.__name__})
            db.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not audit the failed policy run for statement %s", statement_id)
            db.rollback()
        return PolicyResult()
