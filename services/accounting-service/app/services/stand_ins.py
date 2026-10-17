"""R6b.0 stand-ins: provenance of backfilled entries and the owner-deleted marker, derived from the action table.
Read only; the engine and the sweep write the action rows."""
from __future__ import annotations

import collections
import itertools
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationCase, StatementCoverage, StatementLine,
)
from app.services import coverage_service
from app.services.statements import matching

BACKFILL_ACTIONS = frozenset({"backfill_entry"})  # R6c adds its transfer actions
# the automatic creations an owner twin or a replaced event stops (final production finding 2026-10-17, PR #77 S1)
OWNER_TWIN_GUARDED = frozenset({"backfill_entry", "create_system_entry"})
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
    """Events of this statement whose LATEST creation action is reverted with inverse.deleted_by_event (or, retired
    by the sweep's catch-up pass, inverse.deleted_before_watermark) set and no later applied creation exists: the
    owner removed the created entry in the ledger on purpose (R6b.0)."""
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
    return {e for e, (status, inverse) in latest.items()
            if status == "reverted" and (inverse.get("deleted_by_event") or inverse.get("deleted_before_watermark"))}


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


def drift_days(line: StatementLine, entry: LedgerEntry) -> int:
    """The matcher's day distance between the line and the entry (posted dates, or the txn/entry dates when nearer):
    the distance `_conserves` checks against the drift window and the policy against the exact window (C2)."""
    from app.services import reconciliation_service  # local: reconciliation_service imports this module
    return matching._date_distance(reconciliation_service._as_line(line), reconciliation_service.entry_view(entry))


def _conserves(statement: AccountStatement, line: StatementLine, entry: LedgerEntry, rules) -> bool:
    """The entry alone takes the line: an eligible kind for the line, the same flow, within the drift window."""
    from app.services import reconciliation_service  # local: reconciliation_service imports this module
    ml = reconciliation_service._as_line(line)
    me = reconciliation_service.entry_view(entry)
    return (matching._entry_class(me) in matching.eligible_kinds(statement.kind, line.line_kind)
            and matching._same(me.flow, ml.flow) and drift_days(line, entry) <= rules.drift_window_days)


SPLIT_SUBSET_MAX = 8  # members of one group enumerated for a conserving subset; larger groups: the whole group only


def owner_children(db: Session, statement: AccountStatement, accounts: list[int], rules) -> list[LedgerEntry]:
    """Unclaimed child entries (a MOZE fee under a purchase) of the participating accounts in the statement's
    currency, posted within period ± drift_window_days. The matcher claims a child alone for a fee line, so a
    child is a twin of such a line too (fix 2026-10-17: a system fee was booked beside an owner's child fee)."""
    lo = statement.period_start - timedelta(days=rules.drift_window_days)
    hi = statement.period_end + timedelta(days=rules.drift_window_days)
    day = func.coalesce(LedgerEntry.posted_date, LedgerEntry.entry_date)
    claimed = coverage_service.claimed_entry_ids(db)
    rows = db.execute(select(LedgerEntry).where(
        LedgerEntry.account_id.in_(accounts), LedgerEntry.currency == statement.currency,
        LedgerEntry.parent_entry_id.is_not(None), LedgerEntry.kind != "balance_adjustment", day >= lo, day <= hi)
        .order_by(LedgerEntry.id)).scalars().all()
    return [e for e in rows if e.id not in claimed]


def _near(statement: AccountStatement, line: StatementLine, entry: LedgerEntry, rules) -> bool:
    """The entry may record the line at a rounded amount (an FX conversion the owner estimated): an eligible kind,
    the flow within the matcher's near tolerance (max of `near_tolerance_abs` and `near_tolerance_pct`), within
    the near window. Unlike `matching._near_case` no merchant overlap is asked: the guard errs towards not booking."""
    from app.services import reconciliation_service  # local: reconciliation_service imports this module
    ml = reconciliation_service._as_line(line)
    me = reconciliation_service.entry_view(entry)
    if matching._entry_class(me) not in matching.eligible_kinds(statement.kind, line.line_kind):
        return False
    tol = max(rules.near_tolerance_abs, (rules.near_tolerance_pct * abs(ml.flow)).quantize(matching.Q))
    return abs((ml.flow - me.flow).quantize(matching.Q)) <= tol and drift_days(line, entry) <= rules.near_window_days


def _split_twins(statement: AccountStatement, line: StatementLine, population: list[LedgerEntry], rules) -> list[int]:
    """Ids of group members that together take the line: among the unclaimed top-level members of one MOZE group
    within the drift window, a subset of two or more whose flows sum to the line's flow exactly (a purchase the
    owner split into an expense and a receivable, with or without a fee member the statement prints as its own
    line). Groups above SPLIT_SUBSET_MAX members are checked as a whole only."""
    from app.services import reconciliation_service  # local: reconciliation_service imports this module
    flow = reconciliation_service._as_line(line).flow.quantize(matching.Q)
    by_group: dict[int, list[LedgerEntry]] = collections.defaultdict(list)
    for e in population:
        if e.group_id is not None and e.parent_entry_id is None and drift_days(line, e) <= rules.drift_window_days:
            by_group[e.group_id].append(e)
    out: list[int] = []
    for members in by_group.values():
        if len(members) < 2:
            continue
        if len(members) > SPLIT_SUBSET_MAX:
            if sum((Decimal(e.amount) for e in members), Decimal(0)).quantize(matching.Q) == flow:
                out.extend(e.id for e in members)
            continue
        for size in range(2, len(members) + 1):
            for subset in itertools.combinations(members, size):
                if sum((Decimal(e.amount) for e in subset), Decimal(0)).quantize(matching.Q) == flow:
                    out.extend(e.id for e in subset)
    return sorted(set(out))


def owner_twins(db: Session, statement: AccountStatement, line: StatementLine, accounts: list[int], rules) -> list[int]:
    """Ids of the owner entries that may record the line, so the policy must not book it (final production finding
    2026-10-17, widened 2026-10-17 after three double bookings on one card): an entry of `owner_population` (split
    members included — the matcher never claims one alone) or an unclaimed child (`owner_children`) that alone takes
    the line exactly (`_conserves`) or at a near amount (`_near`), and the members of a group subset that together
    take it (`_split_twins`)."""
    population = owner_population(db, statement, accounts, rules)
    candidates = population + owner_children(db, statement, accounts, rules)
    alone = [e.id for e in candidates if _conserves(statement, line, e, rules) or _near(statement, line, e, rules)]
    return sorted(set(alone) | set(_split_twins(statement, line, population, rules)))


def replaced_event(db: Session, statement: AccountStatement, event_id: int | None) -> bool:
    """The event has an applied `replace_stand_in` on this statement: the owner's entry already won it over a
    stand-in, so the policy never backfills it again (the owner decides; a re-created plain entry is re-claimed by
    the matcher)."""
    if event_id is None:
        return False
    return db.execute(
        select(ReconciliationAction.id)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationAction.event_id == event_id,
               ReconciliationAction.action == "replace_stand_in", ReconciliationAction.status == "applied")
        .limit(1)).first() is not None


def owner_may_record(db: Session, statement: AccountStatement, event_id: int | None, line: StatementLine, rules) -> bool:
    """An owner entry may already record the line: the event has an applied replacement (`replaced_event`) or an
    owner twin takes it alone (`owner_twins`). The policy and the policy's own proposals never book such a line;
    under the writer barrier (`BackfillEntryAction` / `CreateSystemEntryAction`) the answer cannot change before the
    booking commits."""
    accounts = coverage_service.participating_accounts(db, statement.account_id)
    return replaced_event(db, statement, event_id) or bool(owner_twins(db, statement, line, accounts, rules))


def shadows(db: Session, statement: AccountStatement, accounts: list[int], rules) -> list[dict]:
    """R6b.5 shadowed_by_stand_in: reciprocally unique owner-entry ↔ stand-in-held line pairs, with the pair's
    `drift_days` (the policy applies only exact-window shadows, C2)."""
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
                        "backfill_action_id": action_id, "drift_days": drift_days(line, e)})
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
