"""R6b.0 stand-ins: provenance of backfilled entries and the owner-deleted marker, derived from the action table.
Read only; the engine and the sweep write the action rows."""
from __future__ import annotations

import collections
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationCase, StatementCoverage, StatementLine,
)
from app.services import coverage_service
from app.services.statements import matching

BACKFILL_ACTIONS = frozenset({"backfill_entry"})  # R6c adds its transfer actions
CREATION_SLOT = "create"


def stand_in_entry_ids(db: Session, account_ids: list[int]) -> set[int]:
    """Entries created by an applied backfill action on a statement of these accounts that still exist."""
    rows = db.execute(
        select(ReconciliationAction.created_entry_ids)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .join(AccountStatement, AccountStatement.id == ReconciliationCase.statement_id)
        .where(AccountStatement.account_id.in_(account_ids), ReconciliationAction.status == "applied",
               ReconciliationAction.action.in_(BACKFILL_ACTIONS))).all()
    ids = {i for (created,) in rows for i in (created or ())}
    if not ids:
        return set()
    return set(db.execute(select(LedgerEntry.id).where(LedgerEntry.id.in_(ids))).scalars())


def creation_for_entry(db: Session, entry_id: int) -> ReconciliationAction | None:
    """The applied creation action (any creation family) whose created_entry_ids names the entry."""
    return db.execute(
        select(ReconciliationAction)
        .where(ReconciliationAction.status == "applied", ReconciliationAction.effect_slot == CREATION_SLOT,
               ReconciliationAction.created_entry_ids.contains([entry_id]))
        .order_by(ReconciliationAction.id.desc())).scalars().first()


def owner_deleted_events(db: Session, statement: AccountStatement) -> set[int]:
    """Events of this statement whose LATEST creation action is reverted with inverse.deleted_by_event set and no
    later applied creation exists: the owner removed the created entry in the ledger on purpose (R6b.0)."""
    rows = db.execute(
        select(ReconciliationAction.event_id, ReconciliationAction.status, ReconciliationAction.inverse)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationAction.effect_slot == CREATION_SLOT,
               ReconciliationAction.event_id.is_not(None))
        .order_by(ReconciliationAction.event_id, ReconciliationAction.id)).all()
    latest: dict[int, tuple[str, dict]] = {}
    for event_id, status, inverse in rows:
        if status in ("applied", "reverted"):
            latest[event_id] = (status, inverse or {})
    return {e for e, (status, inverse) in latest.items() if status == "reverted" and inverse.get("deleted_by_event")}


def retirement_preview(db: Session, statement: AccountStatement) -> dict[str, int]:
    """R6b.7, read only: applied creation actions (RETIRABLE_ACTIONS) of the statement's cases whose created entry
    no longer exists — what the next sweep would retire — counted by action name."""
    rows = db.execute(
        select(ReconciliationAction.action, ReconciliationAction.created_entry_ids)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationAction.status == "applied",
               ReconciliationAction.action.in_(coverage_service.RETIRABLE_ACTIONS))).all()
    ids = {i for _, created in rows for i in (created or ())}
    alive = set(db.execute(select(LedgerEntry.id).where(LedgerEntry.id.in_(ids))).scalars()) if ids else set()
    out: dict[str, int] = {}
    for action, created in rows:
        if any(i not in alive for i in (created or ())):
            out[action] = out.get(action, 0) + 1
    return out


# --- R6b.5 shadow / orphan discovery (own queries: nothing from the matcher result, M1) ------------------------------

def held_by_stand_ins(db: Session, statement: AccountStatement,
                      accounts: list[int]) -> dict[int, tuple[StatementLine, int, int]]:
    """event_id → (current-revision line, stand_in_entry_id, backfill_action_id) for this statement's active coverage
    rows whose entry is a stand-in. Queried directly: these lines are keep_lines for the matcher."""
    ids = stand_in_entry_ids(db, accounts)
    if not ids:
        return {}
    rows = db.execute(
        select(StatementCoverage, StatementLine)
        .join(StatementLine, StatementLine.id == StatementCoverage.line_id)
        .where(StatementCoverage.statement_id == statement.id, StatementCoverage.status == "active",
               StatementCoverage.entry_id.in_(ids), StatementLine.revision_id == statement.current_revision_id)
        .order_by(StatementCoverage.id)).all()
    out = {}
    for cov, line in rows:
        action = creation_for_entry(db, cov.entry_id)
        if action is not None:
            out[line.event_id] = (line, cov.entry_id, action.id)
    return out


def owner_population(db: Session, statement: AccountStatement, accounts: list[int], rules) -> list[LedgerEntry]:
    """Claimable, non-stand-in, top-level, unclaimed entries of the participating accounts in the statement's
    currency, posted within period ± drift_window_days — inside or outside the period alike (M1)."""
    lo = statement.period_start - timedelta(days=rules.drift_window_days)
    hi = statement.period_end + timedelta(days=rules.drift_window_days)
    day = func.coalesce(LedgerEntry.posted_date, LedgerEntry.entry_date)
    claimed = coverage_service.claimed_entry_ids(db) | stand_in_entry_ids(db, accounts)
    rows = db.execute(select(LedgerEntry).where(
        LedgerEntry.account_id.in_(accounts), LedgerEntry.currency == statement.currency,
        LedgerEntry.parent_entry_id.is_(None), LedgerEntry.kind != "balance_adjustment", day >= lo, day <= hi)
        .order_by(LedgerEntry.id)).scalars().all()
    return [e for e in rows if e.id not in claimed]


def _conserves(statement: AccountStatement, line: StatementLine, entry: LedgerEntry, rules) -> bool:
    """The entry alone takes the line: an eligible kind for the line, the same flow, within the drift window."""
    from app.services import reconciliation_service  # local: reconciliation_service imports this module
    ml = reconciliation_service._as_line(line)
    me = reconciliation_service.entry_view(entry)
    return (matching._entry_class(me) in matching.eligible_kinds(statement.kind, line.line_kind)
            and matching._same(me.flow, ml.flow) and matching._date_distance(ml, me) <= rules.drift_window_days)


def shadows(db: Session, statement: AccountStatement, accounts: list[int], rules) -> list[dict]:
    """R6b.5 shadowed_by_stand_in: reciprocally unique owner-entry ↔ stand-in-held line pairs."""
    held = held_by_stand_ins(db, statement, accounts)
    if not held:
        return []
    owners = owner_population(db, statement, accounts, rules)
    pairs = [(e, event_id) for e in owners for event_id, (line, _, _) in held.items()
             if _conserves(statement, line, e, rules)]
    by_owner = collections.Counter(e.id for e, _ in pairs)
    by_event = collections.Counter(ev for _, ev in pairs)
    out = []
    for e, event_id in pairs:
        if by_owner[e.id] == 1 and by_event[event_id] == 1:
            line, stand_in_id, action_id = held[event_id]
            out.append({"entry_id": e.id, "event_id": event_id, "line_id": line.id, "stand_in_entry_id": stand_in_id,
                        "backfill_action_id": action_id})
    return out


def orphans(db: Session, statement: AccountStatement, accounts: list[int]) -> list[dict]:
    """R6b.5 orphaned_stand_in: a stand-in with NO active coverage anywhere whose creating event's current line
    is held by a non-stand-in entry on this statement."""
    ids = stand_in_entry_ids(db, accounts)
    if not ids:
        return []
    claimed = coverage_service.claimed_entry_ids(db)
    rows = db.execute(
        select(StatementCoverage, StatementLine)
        .join(StatementLine, StatementLine.id == StatementCoverage.line_id)
        .where(StatementCoverage.statement_id == statement.id, StatementCoverage.status == "active",
               StatementLine.revision_id == statement.current_revision_id)
        .order_by(StatementCoverage.id)).all()
    by_event = {line.event_id: (line, cov.entry_id) for cov, line in rows
                if cov.entry_id is not None and cov.entry_id not in ids}
    out = []
    for stand_in_id in sorted(ids - claimed):
        action = creation_for_entry(db, stand_in_id)
        if action is None or action.event_id not in by_event:
            continue
        line, claimant = by_event[action.event_id]
        out.append({"entry_id": stand_in_id, "event_id": action.event_id, "line_id": line.id,
                    "backfill_action_id": action.id, "claimant_entry_id": claimant})
    return out
