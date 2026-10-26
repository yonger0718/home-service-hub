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

import calendar
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
    Account, AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationAudit, ReconciliationCase,
    ReconciliationProposal, StatementLine,
)
from app.schemas.reconciliation import ProposalIn
from app.services import coverage_service, ledger_service, proposal_service, settings_service, stand_ins
from app.services import reconciliation_actions as registry
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, ConflictError, NotFoundError, ValidationError
from app.services.reconciliation_actions import OPEN_STATUSES, audit, engine, transfer_actions
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

def _absorbed_by_anchor(db: Session, statement: AccountStatement, line: StatementLine | None) -> bool:
    """Final review C1: the account's applied anchor (`inverse.as_of`) already carries every missing entry dated on
    or before its as_of, so an automatic builder must not book such a line again (the owner still may)."""
    from app.services.reconciliation_actions import structural_actions as sa
    if line is None:
        return False
    as_of = sa.anchor_as_of(db, statement.account_id)
    return as_of is not None and line.posted_date <= as_of


def _next_month(day: date) -> date:
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def _first_live_month(target, account) -> bool:
    """Final review C1: the anchor target starts in the account's first live month — on or before
    `statement_live_from`, or less than one calendar month after it (no live-from date: always)."""
    live_from = account.statement_live_from
    return live_from is None or target.period_start <= live_from or target.period_start < _next_month(live_from)


def _anchor_gate(db: Session, account_id: int) -> bool:
    """The POLICY anchors only a target in the first live month (`_first_live_month`) that no live bank statement of
    the account precedes (an earlier live statement without an opening balance would be absorbed by the anchor);
    an earlier live month still to be ingested is caught by the calendar bound. The owner may anchor any target."""
    from app.services.reconciliation_actions import structural_actions as sa
    target = sa.anchor_target(db, account_id)
    if target is None or not _first_live_month(target, db.get(Account, account_id)):
        return False
    earlier = select(AccountStatement.id).where(
        AccountStatement.account_id == account_id, AccountStatement.kind == "bank", AccountStatement.mode == "live",
        AccountStatement.period_start < target.period_start)
    return db.execute(earlier.limit(1)).first() is None


def _system_entry_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """An open `line_unmatched` with the bank-only hint → book the line as its own fee/interest/reward entry."""
    context = case.context or {}
    if case.kind != "line_unmatched" or context.get("hint") != "bank_only" or context.get("owner_deleted"):
        return None
    line = current_line(db, case, statement)
    if line is None or line.line_kind not in SYSTEM_LINE_KINDS or _absorbed_by_anchor(db, statement, line):
        return None
    if _owner_may_record(db, statement, case, line, rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end)):
        return None  # PR #77 S1: e.g. an owner fee entry that is a split member
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


def _past_grace_in_scope(db: Session, statement: AccountStatement) -> bool:
    """R6b.1 (and R6c `pair_transfer`): `backfill_grace_days` have passed since the statement's period end and its
    account is in `backfill_accounts` (empty = every account)."""
    policy = settings_service.reconciliation_policy(db)
    if statement.period_end + timedelta(days=int(policy["backfill_grace_days"])) > ledger_service._today():
        return False
    accounts = policy.get("backfill_accounts") or []
    return not accounts or statement.account_id in accounts


def _backfill_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """R6b.1 policy column: the owner eligibility plus live mode, the grace, the account list and no owner delete."""
    if case.kind != "line_unmatched" or (case.context or {}).get("owner_deleted"):
        return None
    kind = registry.backfill_eligibility(case)
    if kind is None or statement.mode != "live" or not _past_grace_in_scope(db, statement):
        return None
    line = current_line(db, case, statement)
    if line is None or _absorbed_by_anchor(db, statement, line):
        return None
    rules = rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    if _owner_may_record(db, statement, case, line, rules):
        return None
    return Candidate(case.id, case.kind, case.status, "backfill_entry", {"kind": kind}, _money(line.flow_amount))


def _single_peer(db: Session, case: ReconciliationCase) -> tuple[AccountStatement, StatementLine] | None:
    """The case's only peer candidate (`PairTransferAction.peer_candidates`); None when there is none or several."""
    found = transfer_actions.PairTransferAction.peer_candidates(db, case)
    return found[0] if len(found) == 1 else None


def _pair_transfer_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """R6c §5.3 policy column: an open `needs_peer` line on a live statement past the grace and in the account list
    (`_past_grace_in_scope`, as for a backfill), never an owner delete, with exactly one peer candidate on a statement
    no automatic answer is kept off (`statement_excluded`), no owner entry that may already record the pair
    (`transfer_twin_reason`) and no pairing still applied or owner-deleted on either event (`pair_transfer_block`)
    → pair the two lines; amount = |flow|. Reciprocal uniqueness follows from the apply: it resolves the peer case,
    so the peer statement's run finds nothing to pair."""
    context = case.context or {}
    if case.kind != "line_unmatched" or context.get("hint") != transfer_actions.PEER_HINT \
            or context.get("owner_deleted") or statement.mode != "live" or not _past_grace_in_scope(db, statement):
        return None
    line = current_line(db, case, statement)
    peer = None if line is None else _single_peer(db, case)
    if peer is None:
        return None
    peer_statement, peer_line = peer
    if statement_excluded(db, peer_statement) is not None:  # peer_candidates keeps busy peers; the action refuses them
        return None
    rules = rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    if transfer_actions.transfer_twin_reason(db, rules, (statement, line), peer) is not None \
            or stand_ins.pair_transfer_block(db, [case.event_id, peer_line.event_id]):
        return None
    return Candidate(case.id, case.kind, case.status, "pair_transfer", {"peer_line_id": peer_line.id},
                     _money(line.flow_amount),
                     evidence={"peer_statement_id": peer_statement.id, "peer_line_id": peer_line.id})


def _owner_may_record(db: Session, statement: AccountStatement, case: ReconciliationCase, line: StatementLine,
                      rules: matching.Rules) -> bool:
    """Final production finding 2026-10-17: an owner entry may already record the line — the event has an applied
    `replace_stand_in` (the owner's entry won it once), or an owner twin (`stand_ins.owner_twins`, split members
    included) takes it alone. The automatic backfill would duplicate it; the owner's own backfill stays available."""
    return stand_ins.owner_may_record(db, statement, case.event_id, line, rules)


def refuse_owner_recorded(ctx: Context) -> None:
    """Engine precheck for the owner applying a policy-authored `backfill_entry` / `create_system_entry` proposal
    (`proposal_service`), under the statement and case locks: 409 `owner_may_record` when an owner entry may already
    record the line — a proposal made before the twin or the replacement arrived, applied before the next policy run
    supersedes it. The action re-checks under the writer barrier (`ledger_actions._TwinGuarded`)."""
    if ctx.line is not None and _owner_may_record(ctx.db, ctx.statement, ctx.case, ctx.line, ctx.rules):
        raise CodedConflictError("owner_may_record", f"case {ctx.case.id}")


def _supersede_reason(db: Session, statement: AccountStatement, case: ReconciliationCase, line: StatementLine,
                      rules: matching.Rules, proposal: ReconciliationProposal) -> str | None:
    """Why a pending policy proposal can no longer be applied as written, or None: `owner_may_record` when an owner
    entry may now record the line (`_owner_may_record`) or, for a `pair_transfer`, the pair with its proposed peer
    line (the transfer-twin guard, `transfer_twin_reason`: rules (a)/(b) and the R6b rule on either line);
    `peer_out_of_scope` when that peer line is no longer a peer candidate (claimed, its case closed, its statement
    retired) — the peer statement's changes never move this case's version, so `supersede_stale` cannot see it."""
    if _owner_may_record(db, statement, case, line, rules):
        return "owner_may_record"
    if proposal.action != "pair_transfer":
        return None
    peer = next(((s, other) for s, other in transfer_actions.PairTransferAction.peer_candidates(db, case)
                 if other.id == (proposal.params or {}).get("peer_line_id")), None)
    if peer is None:
        return "peer_out_of_scope"
    return "owner_may_record" if transfer_actions.transfer_twin_reason(db, rules, (statement, line), peer) else None


def _supersede_owner_recorded(db: Session, statement_id: int, *, run_id: int | None) -> int:
    """Pending policy-authored `stand_ins.OWNER_TWIN_GUARDED` proposals (`backfill_entry`, `create_system_entry`,
    `pair_transfer`) of the statement that an owner entry may now record, or a `pair_transfer` whose peer line is no
    longer a candidate (`_supersede_reason`) → superseded, audited `proposal_superseded` (`by`: the reason), and a
    `proposed` case left without a live proposal reopens (so the next run may collect the line again). The case
    keeps its version through reconcile, so `supersede_stale` never sees them. Locks statement → cases (ascending)
    → proposals (the `supersede_stale` order); commits. Returns how many."""
    case_ids = sorted(set(db.execute(
        select(ReconciliationProposal.case_id)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationProposal.case_id)
        .where(ReconciliationCase.statement_id == statement_id, ReconciliationProposal.status == "pending",
               ReconciliationProposal.author == POLICY,
               ReconciliationProposal.action.in_(stand_ins.OWNER_TWIN_GUARDED))).scalars()))
    if not case_ids:
        return 0
    statement = rec._lock_statement(db, statement_id)
    rules = rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    superseded: list[int] = []
    for case_id in case_ids:
        case = db.execute(select(ReconciliationCase).where(ReconciliationCase.id == case_id).with_for_update()
                          .execution_options(populate_existing=True)).scalar_one()
        line = current_line(db, case, statement)
        if line is None:
            continue
        for proposal in proposal_service._pending(db, case.id, author=POLICY):
            by = None if proposal.action not in stand_ins.OWNER_TWIN_GUARDED \
                else _supersede_reason(db, statement, case, line, rules, proposal)
            if by is None:
                continue
            proposal.status, proposal.decided_at = "superseded", func.now()
            audit.record(db, event="proposal_superseded", actor=POLICY, statement_id=statement_id, case_id=case.id,
                         run_id=run_id, detail={"proposal_id": proposal.id, "by": by})
            superseded.append(case.id)
    db.flush()
    proposal_service.reopen_idle(db, superseded)
    db.commit()
    return len(superseded)


def _anchor_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """R6b.2: any open balance_gap of an un-anchored bank account whose target is its first live month (C1);
    amount = |opening delta| at the target."""
    from app.services.reconciliation_actions import structural_actions as sa
    if case.kind != "balance_gap" or statement.kind != "bank" or sa.anchor_applied(db, statement.account_id) \
            or not _anchor_gate(db, statement.account_id):
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
            "entry_id": case.entry_id, "event_id": case.event_id, "drift_days": context.get("drift_days")}


def _replace_candidate(db: Session, case: ReconciliationCase, statement: AccountStatement) -> Candidate | None:
    """R6b.5: an open entry_unmatched case with a stand-in hint → replace the stand-in (amount 0). A shadow's
    evidence carries its `drift_days`: farther than the exact window it is only proposed (C2, `_prefilter`). A hint
    without a `backfill_action_id` is an auto-pay leg's (auto-pay spec §4.5): never replaced, the owner deletes the
    pair."""
    context = case.context or {}
    if case.kind != "entry_unmatched" or context.get("hint") not in REPLACE_HINTS \
            or context.get("backfill_action_id") is None:
        return None
    return Candidate(case.id, case.kind, case.status, "replace_stand_in", {}, REPLACE_AMOUNT, entry_id=None,
                     evidence=_replace_evidence(case))


OWNER_DELETED_GUARDED = frozenset({"create_system_entry", "backfill_entry", "pair_transfer"})  # R6b.0: never re-book an owner delete

POLICY_ACTIONS = {"create_system_entry": _system_entry_candidate, "attach_fee": _attach_fee_candidate,
                  "backfill_entry": _backfill_candidate, "anchor_opening": _anchor_candidate,
                  "replace_stand_in": _replace_candidate, "pair_transfer": _pair_transfer_candidate}


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
    """Every candidate of the statement, by case id (read only; no flag, cap or exclusion is applied here). A
    creation (`create_system_entry`, `backfill_entry`) on an owner-deleted event is never a candidate, so it is
    neither applied nor proposed even when the sweep reopened its case with the old context (final review I3)."""
    cases = db.execute(select(ReconciliationCase).where(
        ReconciliationCase.statement_id == statement.id, ReconciliationCase.status == "open",
        ReconciliationCase.kind.in_(("line_unmatched", "amount_delta", "balance_gap", "entry_unmatched")))
        .order_by(ReconciliationCase.id)).scalars().all()
    deleted = stand_ins.owner_deleted_events(db, statement)
    out = [c for case in cases for make in POLICY_ACTIONS.values() if (c := make(db, case, statement)) is not None
           and not (c.action in OWNER_DELETED_GUARDED and case.event_id in deleted)]
    if policy.get("accept_agent_proposals") is True:
        out += _agent_candidates(db, statement)
    return sorted(out, key=lambda c: (c.case_id, c.proposal_id or 0))


# --- checks -------------------------------------------------------------------------------------------------------

def _rule(policy: dict, action: str) -> dict:
    return (policy.get("actions") or {}).get(action) or {}


def statement_excluded(db: Session, statement: AccountStatement) -> str | None:
    """Why no automatic answer (the policy, an owner rule) may touch the statement now, the currency gate aside:
    `not_live`, `conflict_open`, `needs_recheck` or the kind of an open `EXCLUDING_CASES` case; None otherwise."""
    if statement.mode != "live":
        return "not_live"
    if statement.conflict_open:
        return "conflict_open"
    if statement.needs_recheck:
        return "needs_recheck"
    blocking = db.execute(select(ReconciliationCase.kind).where(
        ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind.in_(EXCLUDING_CASES),
        ReconciliationCase.status.in_(OPEN_STATUSES)).limit(1)).scalar_one_or_none()
    return blocking


def _statement_excluded(db: Session, statement: AccountStatement, policy: dict) -> str | None:
    excluded = statement_excluded(db, statement)
    if excluded is not None:
        return excluded
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
    entry) the bank-only hint on a line of the proposed kind, (for a backfill) still no owner twin or replacement."""
    case, context = ctx.case, ctx.case.context or {}
    if (case.status, case.kind, context.get("entry_id")) != (candidate.status, candidate.kind, candidate.entry_id):
        return False
    if _amount_now(ctx, candidate) != candidate.amount:
        return False
    if candidate.action in OWNER_DELETED_GUARDED and (
            context.get("owner_deleted") or case.event_id in stand_ins.owner_deleted_events(ctx.db, ctx.statement)):
        return False  # re-derived from the action table under the lock: the sweep may have retired it since the collect
    if candidate.action in ("create_system_entry", "backfill_entry") and _absorbed_by_anchor(ctx.db, ctx.statement, ctx.line):
        return False  # C1: an anchor applied since the collect already carries this line
    if candidate.action == "anchor_opening":
        from app.services.reconciliation_actions import structural_actions as sa
        return not sa.anchor_applied(ctx.db, ctx.statement.account_id) and _anchor_gate(ctx.db, ctx.statement.account_id)
    if candidate.action == "replace_stand_in":  # the hint, the backfill action, the entry and the event as collected
        return _replace_evidence(case) == candidate.evidence and _shadow_in_exact_window(ctx)
    if candidate.action == "create_system_entry":
        return context.get("hint") == "bank_only" and ctx.line.line_kind == candidate.params.get("kind") \
            and not _owner_may_record(ctx.db, ctx.statement, case, ctx.line, ctx.rules)
    if candidate.action == "backfill_entry":
        return not context.get("owner_deleted") and not case.candidates \
            and registry.backfill_eligibility(case) == candidate.params.get("kind") \
            and not _owner_may_record(ctx.db, ctx.statement, case, ctx.line, ctx.rules)
    if candidate.action == "pair_transfer":  # still a needs_peer line whose single peer candidate is the collected one
        peer = _single_peer(ctx.db, case)
        return context.get("hint") == transfer_actions.PEER_HINT and ctx.line is not None and peer is not None \
            and {"peer_statement_id": peer[0].id, "peer_line_id": peer[1].id} == candidate.evidence \
            and not stand_ins.pair_transfer_block(ctx.db, [case.event_id, peer[1].event_id])
    return True


def _shadow_in_exact_window(ctx: Context) -> bool:
    """C2, under the locks: a shadow's owner entry is still within the exact window of the line (the case context
    is the last reconcile's; the entry may have moved since without a recheck). Orphans: always true."""
    if (ctx.case.context or {}).get("hint") != "shadowed_by_stand_in":
        return True
    entry = ctx.db.get(LedgerEntry, ctx.case.entry_id) if ctx.case.entry_id is not None else None
    return entry is not None and ctx.line is not None \
        and stand_ins.drift_days(ctx.line, entry) <= ctx.rules.exact_window_days


def _drifted(db: Session, case: ReconciliationCase, candidate: Candidate) -> bool:
    """C2: a shadow whose owner entry lies farther than `exact_window_days` from the line (up to the 15-day drift
    window discovery allows) is proposed, never applied, by the policy (spec decision 1's tiering)."""
    drift = candidate.evidence.get("drift_days") if candidate.action == "replace_stand_in" else None
    if drift is None:
        return False
    statement = db.get(AccountStatement, case.statement_id)
    return drift > rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end).exact_window_days


def _is_proposed(rep: matching.Representation, params: dict) -> bool:
    """The representation is the one the `match` params claim: the group (members only) or the entry — standalone
    or a split member printed apart — alone / with its children."""
    head = rep.rows[0]
    if params.get("group_id") is not None:
        return head.group_id == params["group_id"] and rep.kind in ("group", "installment_group")
    wanted = ("principal_children", "member_children") if params.get("with_children") else ("standalone", "member_alone")
    return head.group_id is None and head.entry_id == params.get("entry_id") and rep.kind in wanted


def _in_candidates(case: ReconciliationCase, params: dict) -> bool:
    key = "group_id" if params.get("group_id") is not None else "entry_id"
    return params.get(key) in {c.get(key) for c in case.candidates or [] if isinstance(c, dict)}


def _single_admissible(ctx: Context, candidate: Candidate) -> None:
    """Ruling 22's validator, read only: the matcher's representations of the line over the current population must
    be exactly one, the proposed one, among the case's candidates. Population and lines are reconcile's own
    (split-members spec §5.6): loaded with only this statement's owned coverage excluded (`rec.owned_coverage`; its
    auto claims stay in, as reconcile re-derives them), the claimable lines (owner-covered ones removed) and the guard
    lines (`rec.guard_lines`), so the split-member decision (`statement_context`) is the one reconcile made; every
    entry claimed anywhere is then passed as `claimed` to `_representations` over the claimable rows. A line a split
    group's incomplete readings block in reconcile (`incomplete_groups` over the structural population, nothing
    claimed) is refused. The guard holders are reconcile's too (holders spec §4.3, `rec.pass_two_holders`): the stable
    ones and a recomputed pass 1's own claims, so the context is reconcile's pass-2 context on unchanged state; this
    statement's stored `auto` rows are never read as holders — they are pass-2 output, and a member claim among them
    would release a group reconcile kept whole (shape 13)."""
    db, statement, line = ctx.db, ctx.statement, ctx.line
    if line is None:
        raise CodedConflictError("not_single_admissible", "the line is no longer printed")
    accounts = coverage_service.participating_accounts(db, statement.account_id)
    current = rec._current_lines(db, statement)
    include_reward = any(row.line_kind == "reward" for row in current)
    manual, owned = rec.owned_coverage(db, statement)
    entries, groups = rec.load_candidates(db, statement, accounts, window_days=matching.population_window_days(ctx.rules),
                                          include_reward=include_reward, exclude=owned)
    accounts_by_id = {a.id: a for a in db.execute(select(Account).where(Account.id.in_(accounts))).scalars()}
    claimable_lines = [rec._as_line(r) for r in current if r.id not in manual]
    plans = rec.plan_map(db, statement.account_id)
    lines_all, holders = rec.pass_two_holders(
        db, statement, accounts, ctx.rules, current, claimable_lines, entries, groups, plans,
        lambda e: rec.proposed_fx_fee(accounts_by_id[e.account_id], e.flow))
    match_ctx = matching.statement_context(statement.kind, claimable_lines, lines_all, entries, groups, ctx.rules,
                                           statement.currency, holders=holders)
    matcher_line = rec._as_line(line)
    reps = matching._representations(statement.kind, matcher_line, [e for e in entries if e.claimable], groups,
                                     plans, coverage_service.claimed_entry_ids(db),
                                     ctx.rules, aside=match_ctx.aside, apart=match_ctx.apart)
    incomplete = matching.incomplete_groups(statement.kind, matcher_line, list(match_ctx.structural), groups, set(),
                                            match_ctx.aside)
    if incomplete:
        raise CodedConflictError("not_single_admissible", f"group_readings_incomplete {incomplete}")
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
    """Unlocked: ("try", None, None), ("propose", None, cap reason | "date_drift") or ("skip", code, reason) — an
    agent's proposal over a cap is skipped (it stays pending for the owner)."""
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
    if _drifted(db, case, candidate):
        return "propose", None, "date_drift"
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
    `per_currency_enabled` is skipped with audit `policy_skipped currency`. First, its own pending creation proposals
    (`stand_ins.OWNER_TWIN_GUARDED`) an owner entry may now record are superseded (`_supersede_owner_recorded`). The budget month is the application
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
    _supersede_owner_recorded(db, statement_id, run_id=run_id)
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
