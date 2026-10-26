"""Shared peer-statement and both-statement lifecycle helpers of the R6c transfer actions (spec §5.1), split out of
`transfer_actions.py` (which re-exports them under the same names): the peer leg and family, the live peer statements
and their complementary lines, the peer-busy predicate, the peer case's resolution and reopening, the claim writer,
the verified-discovery accessor and the revert lock set that adds the stored statements.

The peer-busy predicate is a local copy of `policy_service.statement_excluded` (that module imports the engine)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.models import (
    Account, AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationCase, ReconciliationProposal,
    StatementCoverage, StatementLine,
)
from app.services import coverage_service
from app.services.errors import CodedConflictError
from app.services.statements import matching, retired

from . import OPEN_STATUSES, audit
from .base import Context, LockSet
from .ledger_actions import _match_kind
from .structural_actions import _reopen

BUSY_CASES = ("parse_review", "statement_conflict")  # = policy_service.EXCLUDING_CASES
LINE_CASE_KINDS = ("line_unmatched", "ambiguous", "amount_delta")  # the matcher's cases on a line
CANDIDATE_CHANGED = "candidate_changed"


def peer_leg(db: Session, leg: LedgerEntry) -> LedgerEntry | None:
    """The other top-level entry of the leg's transfer group (None unless there is exactly one)."""
    if leg.transfer_group_id is None:
        return None
    others = db.execute(select(LedgerEntry).where(
        LedgerEntry.transfer_group_id == leg.transfer_group_id, LedgerEntry.id != leg.id,
        LedgerEntry.parent_entry_id.is_(None)).order_by(LedgerEntry.id)).scalars().all()
    return others[0] if len(others) == 1 else None


def family_root(db: Session, account_id: int) -> int:
    """The account whose statements print this account's rows: its combined parent, else itself."""
    account = db.get(Account, account_id)
    return account.combined_account_id if account is not None and account.combined_account_id else account_id


def peer_statements(db: Session, account_id: int, currency: str, start: date, end: date) -> list[AccountStatement]:
    """Live, non-retired statements of `participating_accounts(account_id)` in `currency` whose period intersects
    [start, end], ascending id."""
    accounts = coverage_service.participating_accounts(db, account_id)
    return list(db.execute(select(AccountStatement).where(
        AccountStatement.account_id.in_(accounts), AccountStatement.currency == currency,
        AccountStatement.mode == "live", AccountStatement.current_revision_id.is_not(None),
        AccountStatement.period_start <= end, AccountStatement.period_end >= start,
        ~retired.clause(AccountStatement.id)).order_by(AccountStatement.id)).scalars())


def complementary_lines(db: Session, statements: list[AccountStatement], line: StatementLine, *,
                        kinds: frozenset[str], window_days: int) -> list[tuple[AccountStatement, StatementLine]]:
    """Current-revision lines of `statements` of a kind in `kinds`, flow == −line.flow, posted within `window_days`
    of `line`, with no active coverage; ordered by (day distance, line id)."""
    by_revision = {s.current_revision_id: s for s in statements if s.current_revision_id is not None}
    if not by_revision:
        return []
    covered = exists().where(StatementCoverage.line_id == StatementLine.id, StatementCoverage.status == "active")
    rows = db.execute(select(StatementLine).where(
        StatementLine.revision_id.in_(sorted(by_revision)), StatementLine.line_kind.in_(sorted(kinds)),
        StatementLine.flow_amount == -Decimal(line.flow_amount), ~covered)).scalars().all()
    distance = lambda other: abs((other.posted_date - line.posted_date).days)  # noqa: E731
    found = [(by_revision[other.revision_id], other) for other in rows if distance(other) <= window_days]
    return sorted(found, key=lambda pair: (distance(pair[1]), pair[1].id))


def peer_statement_refusal(statement: AccountStatement, db: Session) -> str | None:
    """`peer_not_live` (historical: never written on), `peer_statement_busy` (conflict, recheck pending, or an open
    parse_review / statement_conflict case — `policy_service.statement_excluded`'s predicate), else None."""
    if statement.mode != "live":
        return "peer_not_live"
    if statement.conflict_open or statement.needs_recheck:
        return "peer_statement_busy"
    busy = db.execute(select(exists().where(
        ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind.in_(BUSY_CASES),
        ReconciliationCase.status.in_(OPEN_STATUSES)))).scalar_one()
    return "peer_statement_busy" if busy else None


def resolve_peer_case(db: Session, ctx: Context, peer_statement: AccountStatement,
                      peer_line: StatementLine) -> int | None:
    """§5.1 step 5: the peer line's open/proposed matcher case is resolved by this action row (`explanation` is an
    enum, so the provenance goes in `note`); its pending proposals are superseded (`by: peer_resolved`)."""
    if ctx.action_id is None:
        raise RuntimeError("resolve_peer_case needs the action row (Context.action_id)")
    case = db.execute(select(ReconciliationCase).where(
        ReconciliationCase.statement_id == peer_statement.id, ReconciliationCase.event_id == peer_line.event_id,
        ReconciliationCase.kind.in_(LINE_CASE_KINDS), ReconciliationCase.status.in_(OPEN_STATUSES))
        .order_by(ReconciliationCase.id).limit(1)).scalar_one_or_none()
    if case is None:
        return None
    action = db.get(ReconciliationAction, ctx.action_id).action
    case.status, case.explanation, case.note = "resolved", None, f"{action} from case {ctx.case.id}"
    case.resolved_by, case.resolved_action_id, case.resolved_at = ctx.actor, ctx.action_id, func.now()
    pending = db.execute(select(ReconciliationProposal).where(
        ReconciliationProposal.case_id == case.id, ReconciliationProposal.status == "pending")
        .order_by(ReconciliationProposal.id)).scalars().all()
    for proposal in pending:
        proposal.status, proposal.decided_at = "superseded", func.now()
        audit.record(db, event="proposal_superseded", actor=ctx.label, statement_id=peer_statement.id, case_id=case.id,
                     action_id=ctx.action_id, run_id=ctx.run_id, request_id=ctx.request_id,
                     detail={"proposal_id": proposal.id, "by": "peer_resolved"})
    db.flush()
    return case.id


def reopen_peer_case(db: Session, action: ReconciliationAction) -> None:
    """The stored peer case row, reopened while it is still resolved by this action."""
    case_id = (action.inverse or {}).get("peer_case_id")
    case = None if case_id is None else db.get(ReconciliationCase, case_id)
    if case is not None and case.status == "resolved" and case.resolved_action_id == action.id:
        _reopen(case)
        db.flush()


def claim_on(db: Session, ctx: Context, statement: AccountStatement, line: StatementLine, entry: LedgerEntry,
             rule: str) -> list[int]:
    """One principal claim of `entry` on `line` of `statement` (the case's or the peer's); the coverage ids."""
    written = coverage_service.write_claim(db, statement, line, [matching.Row(entry.id, "principal", Decimal(entry.amount))],
                                           entries_by_id={entry.id: entry}, match_kind=_match_kind(ctx),
                                           match_rule=rule, run_id=ctx.run_id)
    return [c.id for c in written]


def _has_children(db: Session, entry_id: int) -> bool:
    return db.execute(select(exists().where(LedgerEntry.parent_entry_id == entry_id))).scalar_one()


def verified_discovery(ctx: Context) -> dict:
    """The discovery `validate_before` proved under the locks (`ctx._transfer_discovery`); its peer statements must
    be the ones the engine locked (a rediscovery in `apply` could reach an unlocked statement)."""
    found = ctx._transfer_discovery
    if found is None:  # never: the engine runs validate_before first on this context
        raise RuntimeError("transfer action apply without a verified discovery")
    locked = tuple(ctx.lock_set.statement_ids) if ctx.lock_set is not None else ()
    if tuple(found["peer_statement_ids"]) != locked:
        raise CodedConflictError(CANDIDATE_CHANGED, f"statements {found['peer_statement_ids']}, locked {list(locked)}")
    return found


def stored_statement_ids(db: Session, action: ReconciliationAction) -> list[int]:
    """The action's stored `inverse.statement_ids` that still exist."""
    return [sid for sid in (action.inverse or {}).get("statement_ids", []) if db.get(AccountStatement, sid) is not None]


def with_stored_statements(db: Session, targets: LockSet, action: ReconciliationAction) -> LockSet:
    """§5.1 step 7: the revert locks the stored peer statements too (the base implementation does not)."""
    return replace(targets, statement_ids=tuple(sorted({*targets.statement_ids, *stored_statement_ids(db, action)})))
