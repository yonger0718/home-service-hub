"""R6b.0 stand-ins: provenance of backfilled entries and the owner-deleted marker, derived from the action table.
Read only; the engine and the sweep write the action rows."""
from __future__ import annotations

import collections
import itertools
from datetime import timedelta
from decimal import Decimal
from typing import Iterable

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    AccountStatement, EntryGroup, LedgerEntry, ReconciliationAction, ReconciliationCase, SettlementBooking,
    StatementAutoPay, StatementCoverage, StatementLine,
)
from app.services import coverage_service
from app.services.statements import matching

BACKFILL_ACTIONS = frozenset({"backfill_entry", "pair_transfer"})
# the automatic creations an owner twin or a replaced event stops (final production finding 2026-10-17, PR #77 S1)
OWNER_TWIN_GUARDED = frozenset({"backfill_entry", "create_system_entry", "pair_transfer", "book_settlement",
                                "book_dividend"})
CREATION_SLOT = "create"


def auto_pay_legs(db: Session, account_ids: list[int]) -> dict[int, int]:
    """Auto-pay spec §4.5: entry id → `statement_auto_pay.id` for the legs of `booked` rows whose OWN account is one
    of these (the bank leg on the bank's statements, the card leg on the card family's)."""
    out: dict[int, int] = {}
    for column in (StatementAutoPay.out_entry_id, StatementAutoPay.in_entry_id):
        rows = db.execute(select(LedgerEntry.id, StatementAutoPay.id)
                          .join(StatementAutoPay, column == LedgerEntry.id)
                          .where(StatementAutoPay.status == "booked", LedgerEntry.account_id.in_(account_ids)))
        out.update({entry_id: row_id for entry_id, row_id in rows})
    return out


def settlement_legs(db: Session, account_ids: list[int]) -> dict[int, int]:
    """Portfolio integration spec §4.7: entry id → `settlement_booking.id` for the legs of `booked` settlement
    stand-ins (`kind='settlement'`, `origin='stand_in'`) whose OWN account is one of these (the bank leg on the bank
    account's statements; the target leg only where the target is a participating account). A bank-origin booking
    is no stand-in: its bank leg was booked for its line (`book_settlement`)."""
    out: dict[int, int] = {}
    for column in (SettlementBooking.out_entry_id, SettlementBooking.in_entry_id):
        rows = db.execute(select(LedgerEntry.id, SettlementBooking.id)
                          .join(SettlementBooking, column == LedgerEntry.id)
                          .where(SettlementBooking.status == "booked", SettlementBooking.kind == "settlement",
                                 SettlementBooking.origin == "stand_in", LedgerEntry.account_id.in_(account_ids)))
        out.update({entry_id: row_id for entry_id, row_id in rows})
    return out


def unclaimed_settlement_legs(db: Session, account_id: int) -> dict[int, int]:
    """The one "unclaimed stand-in" query (spec §4.7): bank leg entry id → booking id for the `booked` stand-in rows
    of the account (`origin='stand_in'`, `kind='settlement'`) with no active coverage on EITHER leg. Read without
    locks; a retirement re-reads the claim under the row and leg locks (`trade_settlement_service.retire_pair`)."""
    def covered(column):
        return exists().where(StatementCoverage.entry_id == column, StatementCoverage.status == "active")

    rows = db.execute(
        select(LedgerEntry.id, SettlementBooking.id)
        .join(SettlementBooking, or_(LedgerEntry.id == SettlementBooking.out_entry_id,
                                     LedgerEntry.id == SettlementBooking.in_entry_id))
        .where(SettlementBooking.account_id == account_id, SettlementBooking.status == "booked",
               SettlementBooking.kind == "settlement", SettlementBooking.origin == "stand_in",
               LedgerEntry.account_id == account_id,
               ~covered(SettlementBooking.out_entry_id), ~covered(SettlementBooking.in_entry_id))
        .order_by(SettlementBooking.id)).all()
    return {entry_id: row_id for entry_id, row_id in rows}


def stand_in_entry_ids(db: Session, account_ids: list[int]) -> set[int]:
    """Entries of these accounts that stand in for a printed line until an owner representation wins: those created
    by an applied backfill action on a statement of these accounts that still exist, the booked auto-pay legs
    (`auto_pay_legs`) and the booked settlement stand-in legs (`settlement_legs`)."""
    return (_backfilled_ids(db, account_ids) | set(auto_pay_legs(db, account_ids))
            | set(settlement_legs(db, account_ids)))


def _backfilled_ids(db: Session, account_ids: list[int]) -> set[int]:
    rows = db.execute(
        select(ReconciliationAction.created_entry_ids)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .join(AccountStatement, AccountStatement.id == ReconciliationCase.statement_id)
        .where(AccountStatement.account_id.in_(account_ids), ReconciliationAction.status == "applied",
               ReconciliationAction.action.in_(BACKFILL_ACTIONS))).all()
    ids = {i for (created,) in rows for i in (created or ())}
    if not ids:
        return set()
    return set(db.execute(select(LedgerEntry.id).where(LedgerEntry.id.in_(ids),
                                                       LedgerEntry.account_id.in_(account_ids))).scalars())


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


PAIR_TRANSFER = "pair_transfer"
_DELETED_MARKS = ("deleted_by_event", "deleted_before_watermark")


def _pairs_event(event_ids: list[int]):
    """SQL, on ReconciliationAction: the row's event or its `inverse.peer_event_id` is one of `event_ids`."""
    a = ReconciliationAction
    return or_(a.event_id.in_(event_ids), a.inverse["peer_event_id"].astext.in_([str(i) for i in event_ids]))


def pair_transfer_block(db: Session, event_ids: Iterable[int]) -> bool:
    """R6c occupancy guard (spec §5.1 step 5, option A): True when a `pair_transfer` row names one of the events (its
    own event or `inverse.peer_event_id`) and is still `applied`, or is `reverted` with an owner-delete mark
    (`deleted_by_event` / `deleted_before_watermark`) and no later applied creation exists for that event — a
    creation row of that event, or a later `pair_transfer` naming it as its peer event. An ordinary engine revert
    (no mark) blocks nothing."""
    ids = sorted({int(i) for i in event_ids if i is not None})
    if not ids:
        return False
    a = ReconciliationAction
    rows = db.execute(select(a.id, a.event_id, a.status, a.inverse).where(
        a.action == PAIR_TRANSFER, a.status.in_(("applied", "reverted")), _pairs_event(ids))
        .order_by(a.id)).all()
    for row_id, event_id, status, inverse in rows:
        if status == "applied":
            return True
        inverse = inverse or {}
        if not any(mark in inverse for mark in _DELETED_MARKS):
            continue
        for event in sorted({event_id, inverse.get("peer_event_id")} & set(ids)):
            later = db.execute(select(exists().where(
                a.id > row_id, a.status == "applied", a.effect_slot == CREATION_SLOT, _pairs_event([event])))).scalar_one()
            if not later:
                return True
    return False


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
                      accounts: list[int]) -> dict[int, tuple[StatementLine, int, int | None]]:
    """event_id → (current-revision line, stand_in_entry_id, backfill_action_id) for this statement's active coverage
    rows whose entry is a stand-in. Queried directly: these lines are keep_lines for the matcher. An auto-pay leg has
    no creating action: its backfill_action_id is None."""
    ids = stand_in_entry_ids(db, accounts)
    if not ids:
        return {}
    legs = auto_pay_legs(db, accounts)
    rows = db.execute(
        select(StatementCoverage, StatementLine)
        .join(StatementLine, StatementLine.id == StatementCoverage.line_id)
        .where(StatementCoverage.statement_id == statement.id, StatementCoverage.status == "active",
               StatementCoverage.entry_id.in_(ids), StatementLine.revision_id == statement.current_revision_id)
        .order_by(StatementCoverage.id)).all()
    out = {}
    for cov, line in rows:
        if cov.entry_id in legs:
            out[line.event_id] = (line, cov.entry_id, None)
            continue
        action = creation_for_entry(db, cov.entry_id)
        if action is not None:
            out[line.event_id] = (line, cov.entry_id, action.id)
    return out


def owner_population(db: Session, statement: AccountStatement, accounts: list[int], rules, *,
                     claimed: set[int] | None = None) -> list[LedgerEntry]:
    """Claimable, non-stand-in, top-level, unclaimed entries of the participating accounts in the statement's
    currency, posted within period ± drift_window_days — inside or outside the period alike (M1). `claimed`: the
    active claimed-entry snapshot a caller already holds (`owner_twins` takes it once for both populations)."""
    lo = statement.period_start - timedelta(days=rules.drift_window_days)
    hi = statement.period_end + timedelta(days=rules.drift_window_days)
    day = func.coalesce(LedgerEntry.posted_date, LedgerEntry.entry_date)
    claimed = (coverage_service.claimed_entry_ids(db) if claimed is None else claimed) | stand_in_entry_ids(db, accounts)
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


def owner_children(db: Session, statement: AccountStatement, accounts: list[int], rules, *,
                   claimed: set[int] | None = None) -> list[LedgerEntry]:
    """Unclaimed child entries (a MOZE fee under a purchase, its parent claimed or not) of the participating
    accounts in the statement's currency, posted within period ± drift_window_days. The matcher claims a child alone
    for a fee line, so a child is a twin of such a line too (fix 2026-10-17: a system fee was booked beside an
    owner's child fee). `claimed` as in `owner_population`."""
    lo = statement.period_start - timedelta(days=rules.drift_window_days)
    hi = statement.period_end + timedelta(days=rules.drift_window_days)
    day = func.coalesce(LedgerEntry.posted_date, LedgerEntry.entry_date)
    claimed = coverage_service.claimed_entry_ids(db) if claimed is None else claimed
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


def _split_twins(db: Session, statement: AccountStatement, line: StatementLine, population: list[LedgerEntry],
                 rules) -> list[int]:
    """Ids of `split` group members that together take the line: among the unclaimed, in-window, top-level members
    of one MOZE `split` group (never a reward_claim or installment group) whose kinds are each eligible for the
    line (`matching.eligible_kinds`), a subset of two or more whose flows sum to the line's flow exactly (within the
    drift window) or within the matcher's near tolerance (within the near window) — a purchase the owner split
    into an expense and a receivable, with or without a fee member the statement prints as its own line, estimated
    in TWD or converted exactly. Groups whose eligible population exceeds SPLIT_SUBSET_MAX are checked as a whole
    only."""
    from app.services import reconciliation_service  # local: reconciliation_service imports this module
    ml = reconciliation_service._as_line(line)
    flow = ml.flow.quantize(matching.Q)
    gate = matching.eligible_kinds(statement.kind, line.line_kind)
    by_group: dict[int, list[LedgerEntry]] = collections.defaultdict(list)
    for e in population:
        if e.group_id is not None and e.parent_entry_id is None and drift_days(line, e) <= rules.drift_window_days \
                and matching._entry_class(reconciliation_service.entry_view(e)) in gate:
            by_group[e.group_id].append(e)
    if not by_group:
        return []
    split_ids = set(db.execute(select(EntryGroup.id).where(EntryGroup.id.in_(sorted(by_group)),
                                                            EntryGroup.kind == "split")).scalars())
    tol = max(rules.near_tolerance_abs, (rules.near_tolerance_pct * abs(flow)).quantize(matching.Q))

    def takes(subset) -> bool:
        total = sum((Decimal(e.amount) for e in subset), Decimal(0)).quantize(matching.Q)
        if total == flow:
            return True
        # 2026-10-19: a purchase abroad split into an expense and a receivable, estimated in TWD without the FX
        # fee, is a twin at a near amount too — bound by the near window like `_near`
        return abs(total - flow) <= tol and min(drift_days(line, e) for e in subset) <= rules.near_window_days

    out: list[int] = []
    for group_id, members in by_group.items():
        if group_id not in split_ids:
            continue
        if len(members) < 2:
            continue
        if len(members) > SPLIT_SUBSET_MAX:
            if takes(members):
                out.extend(e.id for e in members)
            continue
        for size in range(2, len(members) + 1):
            for subset in itertools.combinations(members, size):
                if takes(subset):
                    out.extend(e.id for e in subset)
    return sorted(set(out))


def owner_twins(db: Session, statement: AccountStatement, line: StatementLine, accounts: list[int], rules) -> list[int]:
    """Ids of the owner entries that may record the line, so the policy must not book it (final production finding
    2026-10-17, widened 2026-10-17 after three double bookings on one card): an entry of `owner_population` (split
    members included — the matcher never claims one alone) or an unclaimed child (`owner_children`) that alone takes
    the line exactly (`_conserves`) or at a near amount (`_near`), and the members of a group subset that together
    take it, exactly or at a near amount (`_split_twins`)."""
    claimed = coverage_service.claimed_entry_ids(db)  # one snapshot for both populations (review S2)
    population = owner_population(db, statement, accounts, rules, claimed=claimed)
    candidates = population + owner_children(db, statement, accounts, rules, claimed=claimed)
    alone = [e.id for e in candidates if _conserves(statement, line, e, rules) or _near(statement, line, e, rules)]
    return sorted(set(alone) | set(_split_twins(db, statement, line, population, rules)))


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
    is held by a non-stand-in entry on this statement. Auto-pay spec §4.5: also every auto-pay leg of `accounts` with
    no active coverage anywhere whose `entry_date` lies in the statement's period and whose row has no twin (the
    settings' `twin_window_days`) — no event, line or claimant; `auto_pay_id` names the row. A leg waiting for a later
    statement is not an orphan. Portfolio integration spec §4.7: likewise every unclaimed settlement stand-in's bank
    leg (`_settlement_orphans`, `settlement_booking_id` names the row)."""
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
    return out + _auto_pay_orphans(db, statement, accounts, claimed) + _settlement_orphans(db, statement, accounts,
                                                                                         claimed)


def _auto_pay_orphans(db: Session, statement: AccountStatement, accounts: list[int], claimed: set[int]) -> list[dict]:
    from app.services import auto_pay_service, settings_service  # local: auto_pay_service imports this module

    legs = {entry_id: row_id for entry_id, row_id in auto_pay_legs(db, accounts).items() if entry_id not in claimed}
    if not legs:
        return []
    in_period = set(db.execute(select(LedgerEntry.id).where(
        LedgerEntry.id.in_(legs), LedgerEntry.entry_date.between(statement.period_start, statement.period_end)))
        .scalars())
    window = int(settings_service.read_auto_pay_settings(db)["twin_window_days"])
    out = []
    for entry_id in sorted(in_period):
        row = db.get(StatementAutoPay, legs[entry_id])
        if auto_pay_service.find_twins(
                db, card_account_id=row.card_account_id, pay_account_id=row.pay_account_id, amount=row.amount,
                pay_date=row.pay_date, window_days=window,
                exclude=[i for i in (row.out_entry_id, row.in_entry_id) if i is not None]):
            continue
        out.append({"entry_id": entry_id, "event_id": None, "line_id": None, "backfill_action_id": None,
                    "auto_pay_id": row.id, "claimant_entry_id": None})
    return out


def _settlement_orphans(db: Session, statement: AccountStatement, accounts: list[int], claimed: set[int]) -> list[dict]:
    """Spec §4.7 "Bank line": the bank legs of the statement account's unclaimed settlement stand-ins
    (`unclaimed_settlement_legs`, neither leg covered anywhere) whose `entry_date` lies in the period and whose row has
    no settlement twin (`trade_settlement_service.find_twins`, the settlement settings' `twin_window_days`): no
    event, line or claimant; `settlement_booking_id` names the row (the auto-pay orphan surfacing)."""
    from app.services import trade_settlement_service as tss  # local: trade_settlement_service imports this module

    legs = {entry_id: row_id for entry_id, row_id in unclaimed_settlement_legs(db, statement.account_id).items()
            if entry_id not in claimed}
    if not legs:
        return []
    in_period = set(db.execute(select(LedgerEntry.id).where(
        LedgerEntry.id.in_(legs), LedgerEntry.entry_date.between(statement.period_start, statement.period_end)))
        .scalars())
    window = tss.twin_window_days(db)
    out = []
    for entry_id in sorted(in_period):
        row = db.get(SettlementBooking, legs[entry_id])
        if tss.find_twins(db, account_id=row.account_id, target_account_id=row.target_account_id,
                          amount=row.net_amount, settlement_date=row.settlement_date, window_days=window,
                          exclude=[i for i in (row.out_entry_id, row.in_entry_id) if i is not None]):
            continue
        out.append({"entry_id": entry_id, "event_id": None, "line_id": None, "backfill_action_id": None,
                    "settlement_booking_id": row.id, "claimant_entry_id": None})
    return out
