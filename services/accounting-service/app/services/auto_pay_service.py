"""Card statement auto-pay (spec 2026-10-26-statement-auto-pay-design §4.1–§4.4): book the card payment a linked
bank account makes on the due date, as a system transfer pair, before either statement prints it.

- Candidate (§4.1): a live card statement (`kind='card'`, `mode='live'`) with `due_date` and `statement_total > 0`
  whose pay date (`due_date` moved to the first business day on or after it, §4.3) is on or before today, and that
  has no `statement_auto_pay` row or only a `skipped` row with a transient reason (§4.2). Final rows (`booked`,
  `retired`, `owner_deleted`, `owner_edited`) and terminal skips are never re-evaluated.
- Gates (§4.4 step 3), in order: cutoff → duplicate_statement → header_disputed → no_link → link_ineligible → twin →
  over_cap → monthly_budget. `duplicate_statement` looks at every final row of the card (owner-deleted and retired
  pairs too), pay dates strictly within 20 days. A transient skip turns `expired` once `today > pay_date +
  retry_days`, even if its condition has cleared.
- Twin (§4.1): another ledger representation of the same payment, whatever its source; a `transfer_out` on the pay
  account counts only when its peer leg is on the card family or it has no peer (§8 errata).
- Booking (§4.4 step 4): a transfer pay account → card master, `source='statement'`, `name='自動扣繳'`, both legs
  dated on the pay date, the card leg posted on the next business day; the `booked` row and an `auto_pay_booked`
  audit row in the same transaction.
- Housekeeping (§4.4 step 0): `check_row` moves a `booked` row to `owner_deleted` / `owner_edited`, retires the pair
  when a twin appears (`retire_pair`: lock the row and its legs, re-validate, release the legs' coverage, delete
  them, `retired`; nothing when the locked re-check fails), copies action edits of the legs onto the row, or flags
  a changed statement header once.

Locks: `book` takes the shared ledger-import advisory key (as `schedule_posting.post_instance` does) so a booking
never interleaves with a MOZE backup import; the job takes it too, around each statement's transaction, and a second
shared xact lock in one transaction is harmless. Nothing here commits; callers own the transaction (flush only).
The UNIQUE `statement_id` and the 20-day exclusion constraint (on `booked` rows) make a concurrent duplicate fail the
second commit.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from psycopg2.errors import LockNotAvailable
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, aliased

from ..models import Account, AccountStatement, LedgerEntry, ReconciliationCase, StatementAutoPay
from ..schemas.writes import TransferIn
from . import coverage_service, entry_write_service, settings_service, stand_ins, transfer_service
from .business_days import next_business_day
from .entry_write_service import STATEMENT_SOURCE
from .errors import CodedConflictError
from .reconciliation_actions import audit
from .schedule_locks import take_import_key_shared
from .statements import matching

AUTO_PAY_NAME = "自動扣繳"
ACTOR = "auto_pay"
DUPLICATE_DAYS = 20  # two final pay dates of one card strictly fewer than 20 days apart are one bill (§4.2)
TERMINAL_REASONS = frozenset({"twin", "cutoff", "duplicate_statement", "expired"})
TRANSIENT_REASONS = frozenset({"header_disputed", "no_link", "link_ineligible", "over_cap", "monthly_budget"})
FINAL_STATUSES = frozenset({"booked", "retired", "owner_deleted", "owner_edited"})
HEADER_CASE_KINDS = ("parse_review", "statement_conflict")
PAIR_BUSY = "auto_pay_busy"
HEADER_CHANGED = "header_changed"  # the booked row's reason once the header change is audited (§4.4 step 0)
TWIN_LOOKOUT_EXTRA_DAYS = 7  # the job's twin test runs until pay_date + twin_window_days + 7; the sweep has no window

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    statement_id: int
    card_account_id: int
    card_name: str
    currency: str
    pay_account_id: int | None
    amount: Decimal
    due_date: date
    pay_date: date
    period_end: date
    existing_reason: str | None


# --- calendar -------------------------------------------------------------------------------------------------------


def calendar_sets(settings: dict) -> tuple[frozenset[date], frozenset[date]]:
    """The settings' `extra_holidays` and `extra_workdays` (ISO strings) as date sets."""
    return (frozenset(date.fromisoformat(day) for day in settings["extra_holidays"]),
            frozenset(date.fromisoformat(day) for day in settings["extra_workdays"]))


def _next_business_day(day: date, settings: dict) -> date:
    holidays, workdays = calendar_sets(settings)
    return next_business_day(day, extra_holidays=holidays, extra_workdays=workdays)


def pay_date_for(due_date: date, settings: dict) -> date:
    """The debit day: the first business day on or after the due date (§4.3)."""
    return _next_business_day(due_date, settings)


# --- candidates -----------------------------------------------------------------------------------------------------


def _live_card_statements():
    return (select(AccountStatement, Account)
            .join(Account, Account.id == AccountStatement.account_id)
            .where(AccountStatement.kind == "card", AccountStatement.mode == "live"))


def candidates(db: Session, today: date, settings: dict) -> list[Candidate]:
    """Auto-pay candidates due by `today`, ordered (pay_date, statement_id). The card's link is read but not
    required (`pay_account_id` None then; the `no_link` gate decides)."""
    rows = db.execute(
        _live_card_statements()
        .add_columns(StatementAutoPay.reason)
        .outerjoin(StatementAutoPay, StatementAutoPay.statement_id == AccountStatement.id)
        .where(AccountStatement.due_date.is_not(None), AccountStatement.due_date <= today,
               AccountStatement.statement_total > 0,
               or_(StatementAutoPay.id.is_(None),
                   and_(StatementAutoPay.status == "skipped", StatementAutoPay.reason.in_(TRANSIENT_REASONS))))
    ).all()
    found = []
    for statement, card, reason in rows:
        pay_date = pay_date_for(statement.due_date, settings)
        if pay_date > today:
            continue
        found.append(Candidate(
            statement_id=statement.id, card_account_id=card.id, card_name=card.name, currency=card.currency,
            pay_account_id=card.auto_pay_account_id, amount=statement.statement_total, due_date=statement.due_date,
            pay_date=pay_date, period_end=statement.period_end, existing_reason=reason))
    return sorted(found, key=lambda c: (c.pay_date, c.statement_id))


def no_due_date(db: Session) -> list[int]:
    """Live card statements of a linked master without a parsed due date (a parser gap; the dry run lists them)."""
    rows = db.execute(_live_card_statements()
                      .with_only_columns(AccountStatement.id)
                      .where(AccountStatement.due_date.is_(None), Account.auto_pay_account_id.is_not(None))
                      .order_by(AccountStatement.id))
    return list(rows.scalars())


# --- twins ----------------------------------------------------------------------------------------------------------


def find_twins(db: Session, *, card_account_id: int, pay_account_id: int, amount: Decimal, pay_date: date,
               window_days: int, exclude: Iterable[int] = ()) -> list[int]:
    """Top-level entries that already represent this payment (§4.1), whatever their source, with `entry_date` within
    `window_days` of the pay date: a `transfer_in` of `amount` on the card family, or on the pay account a
    `transfer_out` of `-amount` whose peer leg (the same `transfer_group_id`'s `transfer_in`) is on the card family
    or that has no peer leg at all, or a statement-booked `expense` of `-amount` (a `backfill_entry` stand-in). A
    `transfer_out` to another account (savings, another card's payment) is not a twin (§8 errata)."""
    family = coverage_service.participating_accounts(db, card_account_id)
    window = timedelta(days=window_days)
    peer = aliased(LedgerEntry)
    peer_leg = and_(peer.transfer_group_id == LedgerEntry.transfer_group_id, peer.id != LedgerEntry.id,
                    peer.kind == "transfer_in")
    paid_to_family_or_peerless = or_(LedgerEntry.transfer_group_id.is_(None),
                                     ~exists().where(peer_leg),
                                     exists().where(peer_leg, peer.account_id.in_(family)))
    query = select(LedgerEntry.id).where(
        LedgerEntry.parent_entry_id.is_(None),
        LedgerEntry.entry_date.between(pay_date - window, pay_date + window),
        or_(and_(LedgerEntry.account_id.in_(family), LedgerEntry.kind == "transfer_in",
                 LedgerEntry.amount == amount),
            and_(LedgerEntry.account_id == pay_account_id, LedgerEntry.amount == -amount,
                 or_(and_(LedgerEntry.kind == "transfer_out", paid_to_family_or_peerless),
                     and_(LedgerEntry.kind == "expense", LedgerEntry.source == STATEMENT_SOURCE)))))
    excluded = list(exclude)
    if excluded:
        query = query.where(LedgerEntry.id.not_in(excluded))
    return list(db.scalars(query.order_by(LedgerEntry.id)))


# --- gates ----------------------------------------------------------------------------------------------------------


def _duplicate_final(db: Session, cand: Candidate) -> bool:
    """Another statement of the same card already has a final row (`booked`, `retired`, `owner_deleted`,
    `owner_edited`) whose pay date is strictly within DUPLICATE_DAYS of the candidate's: one bill (§4.2). A pair the
    owner deleted or a twin retired still counts, so a re-parsed duplicate never books the bill again. (The DB
    exclusion constraint covers `booked` rows only.)"""
    span = timedelta(days=DUPLICATE_DAYS)
    return db.scalar(select(StatementAutoPay.id).where(
        StatementAutoPay.card_account_id == cand.card_account_id, StatementAutoPay.status.in_(FINAL_STATUSES),
        StatementAutoPay.statement_id != cand.statement_id,
        StatementAutoPay.pay_date > cand.pay_date - span, StatementAutoPay.pay_date < cand.pay_date + span,
    ).limit(1)) is not None


def _header_disputed(db: Session, statement_id: int) -> bool:
    return db.scalar(select(ReconciliationCase.id).where(
        ReconciliationCase.statement_id == statement_id, ReconciliationCase.kind.in_(HEADER_CASE_KINDS),
        ReconciliationCase.status.in_(coverage_service.OPEN_CASE_STATUSES)).limit(1)) is not None


def _link_ineligible(db: Session, cand: Candidate) -> bool:
    target = db.get(Account, cand.pay_account_id)
    return target is None or target.is_credit or target.is_archived or target.currency != cand.currency


def _booked_in_month(db: Session, day: date) -> Decimal:
    first = day.replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    total = db.scalar(select(func.coalesce(func.sum(StatementAutoPay.amount), 0)).where(
        StatementAutoPay.status == "booked", StatementAutoPay.pay_date >= first,
        StatementAutoPay.pay_date < next_first))
    return Decimal(total)


def _first_failing_gate(db: Session, cand: Candidate, settings: dict) -> str | None:
    enabled_from = settings["enabled_from"]
    if enabled_from is not None and cand.pay_date < date.fromisoformat(enabled_from):
        return "cutoff"
    if _duplicate_final(db, cand):
        return "duplicate_statement"
    if _header_disputed(db, cand.statement_id):
        return "header_disputed"
    if cand.pay_account_id is None:
        return "no_link"
    if _link_ineligible(db, cand):
        return "link_ineligible"
    if find_twins(db, card_account_id=cand.card_account_id, pay_account_id=cand.pay_account_id, amount=cand.amount,
                  pay_date=cand.pay_date, window_days=int(settings["twin_window_days"])):
        return "twin"
    if cand.amount > Decimal(settings["max_amount"]):
        return "over_cap"
    if _booked_in_month(db, cand.pay_date) + cand.amount > Decimal(settings["monthly_total"]):
        return "monthly_budget"
    return None


def gate(db: Session, cand: Candidate, today: date, settings: dict) -> str | None:
    """The first gate the candidate fails (§4.4 order), or None to book. `cutoff` is not checked while
    `enabled_from` is None (the host dry run runs before the settings PUT). `duplicate_statement` counts every final
    row of the card (`booked`, `retired`, `owner_deleted`, `owner_edited`), not only booked ones. A transient
    reason, found now or stored on the existing `skipped` row, becomes `expired` once `today > pay_date + retry_days`
    even if the condition has cleared (§4.2); a terminal reason found now wins over `expired`. A new candidate (no
    row) past retry_days books."""
    reason = _first_failing_gate(db, cand, settings)
    past_retry = today > cand.pay_date + timedelta(days=int(settings["retry_days"]))
    if past_retry and (reason in TRANSIENT_REASONS or (reason is None and cand.existing_reason in TRANSIENT_REASONS)):
        return "expired"
    return reason


# --- booking --------------------------------------------------------------------------------------------------------


def _row_for(db: Session, cand: Candidate) -> StatementAutoPay:
    """The statement's (transient `skipped`) row to rewrite in place, or a new one added to the session."""
    row = db.scalar(select(StatementAutoPay).where(StatementAutoPay.statement_id == cand.statement_id))
    if row is None:
        row = StatementAutoPay(statement_id=cand.statement_id)
        db.add(row)
    return row


def _figures(cand: "Candidate | StatementAutoPay") -> dict:
    return {"card_account_id": cand.card_account_id, "pay_account_id": cand.pay_account_id,
            "amount": str(cand.amount), "pay_date": cand.pay_date.isoformat()}


def book(db: Session, cand: Candidate, settings: dict) -> StatementAutoPay:
    """Book the auto-pay pair and its `booked` row, audit `auto_pay_booked`. The caller has run `gate` (a linked,
    eligible pay account). Flushes, never commits."""
    take_import_key_shared(db)
    group_id = transfer_service.create_transfer(db, TransferIn(
        from_account_id=cand.pay_account_id, to_account_id=cand.card_account_id, out_amount=cand.amount,
        entry_date=cand.pay_date, posted_date=cand.pay_date, name=AUTO_PAY_NAME,
        description=f"{cand.card_name} {cand.period_end:%Y-%m-%d} 帳單 {AUTO_PAY_NAME}",
    ), source=STATEMENT_SOURCE, remember=False)
    out_leg, in_leg = transfer_service.transfer_legs(db, group_id)
    in_leg.posted_date = _next_business_day(cand.pay_date + timedelta(days=1), settings)  # the issuer's posting day
    out_leg.needs_review = in_leg.needs_review = False
    row = _row_for(db, cand)
    row.card_account_id, row.pay_account_id = cand.card_account_id, cand.pay_account_id
    row.amount, row.due_date, row.pay_date = cand.amount, cand.due_date, cand.pay_date
    row.transfer_group_id, row.out_entry_id, row.in_entry_id = group_id, out_leg.id, in_leg.id
    row.status, row.reason, row.settings_version = "booked", None, settings["version"]
    db.flush()
    audit.record(db, event="auto_pay_booked", actor=ACTOR, statement_id=cand.statement_id,
                 detail={**_figures(cand), "out_entry_id": out_leg.id, "in_entry_id": in_leg.id})
    return row


def record_skip(db: Session, cand: Candidate, reason: str, settings: dict) -> StatementAutoPay:
    """Insert or rewrite the statement's `skipped` row; audit `auto_pay_skipped` only when the reason changed.

    `statement_auto_pay.pay_account_id` is NOT NULL, so a `no_link` skip (no pay account) stores the card itself
    (`card_account_id`) in that column; the audit detail keeps the real `pay_account_id` (None)."""
    row = _row_for(db, cand)
    row.card_account_id = cand.card_account_id
    row.pay_account_id = cand.pay_account_id if cand.pay_account_id is not None else cand.card_account_id
    row.amount, row.due_date, row.pay_date = cand.amount, cand.due_date, cand.pay_date
    row.status, row.reason, row.settings_version = "skipped", reason, settings["version"]
    db.flush()
    if reason != cand.existing_reason:
        audit.record(db, event="auto_pay_skipped", actor=ACTOR, statement_id=cand.statement_id,
                     detail={"reason": reason, **_figures(cand)})
    return row


def decide(db: Session, cand: Candidate, today: date, settings: dict, *, dry_run: bool = False) -> dict:
    """Gate the candidate, then book it or record the skip; with `dry_run` report the decision and write nothing."""
    reason = gate(db, cand, today, settings)
    if dry_run:
        decision = "would_book" if reason is None else "would_skip"
    elif reason is None:
        book(db, cand, settings)
        decision = "booked"
    else:
        record_skip(db, cand, reason, settings)
        decision = "skipped"
    return {"statement_id": cand.statement_id, "card_account_id": cand.card_account_id,
            "pay_account_id": cand.pay_account_id, "amount": str(cand.amount),
            "pay_date": cand.pay_date.isoformat(), "decision": decision, "reason": reason}


# --- housekeeping ---------------------------------------------------------------------------------------------------


def booked_rows(db: Session) -> list[StatementAutoPay]:
    """The `booked` rows, in id order (the job checks each one every run)."""
    return list(db.scalars(select(StatementAutoPay).where(StatementAutoPay.status == "booked")
                           .order_by(StatementAutoPay.id)))


def lock_pair(db: Session, row: StatementAutoPay) -> tuple[LedgerEntry | None, LedgerEntry | None]:
    """The row's (out, in) legs locked in ONE `SELECT … FOR UPDATE NOWAIT` ordered by id (the `locked_with_legs`
    order), refreshed from the database; a leg that is gone (its id column NULL, or the row deleted) comes back None.
    A leg another transaction holds raises `auto_pay_busy`; the savepoint leaves the session usable."""
    ids = [i for i in (row.out_entry_id, row.in_entry_id) if i is not None]
    if not ids:
        return None, None
    try:
        with db.begin_nested():
            legs = {leg.id: leg for leg in db.scalars(
                select(LedgerEntry).where(LedgerEntry.id.in_(ids)).order_by(LedgerEntry.id)
                .with_for_update(nowait=True).execution_options(populate_existing=True))}
    except OperationalError as exc:
        if isinstance(exc.orig, LockNotAvailable):
            raise CodedConflictError(PAIR_BUSY, f"a leg of auto-pay row {row.id} is locked by another write") from exc
        raise
    return legs.get(row.out_entry_id), legs.get(row.in_entry_id)


def _lock_row(db: Session, row_id: int) -> StatementAutoPay | None:
    """The auto-pay row locked `FOR UPDATE NOWAIT` and refreshed from the database (None when it is gone). A row
    another transaction holds raises `auto_pay_busy`; the savepoint leaves the session usable."""
    try:
        with db.begin_nested():
            return db.scalar(select(StatementAutoPay).where(StatementAutoPay.id == row_id)
                             .with_for_update(nowait=True).execution_options(populate_existing=True))
    except OperationalError as exc:
        if isinstance(exc.orig, LockNotAvailable):
            raise CodedConflictError(PAIR_BUSY, f"auto-pay row {row_id} is locked by another write") from exc
        raise


def retire_pair(db: Session, row: StatementAutoPay, *, reason: str, run_id: int | None = None,
                window_days: int | None = None) -> set[int] | None:
    """Delete the row's pair and mark it `retired`: lock the row, then both legs, and re-validate under the locks —
    the row still `booked`, both legs present and `source='statement'`, and for `reason="twin"` a twin still within
    `window_days` (default: the settings' `twin_window_days`) of the pay date. If any check fails (another retirer,
    an owner delete or PUT /transfers landed after the caller's unlocked check) return None and write nothing.
    Otherwise release the coverage the legs hold (reason `auto_pay:<reason>`), delete them, flag the released
    statements `needs_recheck`, audit `auto_pay_retired`, and return the released statement ids (possibly empty).
    A row or leg another transaction holds raises `auto_pay_busy`. Flushes, never commits."""
    row = _lock_row(db, row.id)
    if row is None or row.status != "booked":
        return None
    out_leg, in_leg = lock_pair(db, row)
    if out_leg is None or in_leg is None or STATEMENT_SOURCE != out_leg.source or STATEMENT_SOURCE != in_leg.source:
        return None
    if reason == "twin":
        if window_days is None:
            window_days = int(settings_service.read_auto_pay_settings(db)["twin_window_days"])
        if not find_twins(db, card_account_id=row.card_account_id, pay_account_id=row.pay_account_id,
                          amount=row.amount, pay_date=row.pay_date, window_days=window_days,
                          exclude=(out_leg.id, in_leg.id)):
            return None
    # delete_entries_cascade ends with expire_all and the leg FKs are ON DELETE SET NULL: copy everything first
    row_id, statement_id = row.id, row.statement_id
    out_id, in_id = out_leg.id, in_leg.id
    legs = [out_id, in_id]
    released = coverage_service.release_entries(db, legs, reason=f"auto_pay:{reason}")
    entry_write_service.delete_entries_cascade(db, legs)
    row = db.get(StatementAutoPay, row_id)
    row.status, row.reason = "retired", reason
    for sid in released:
        db.get(AccountStatement, sid).needs_recheck = True
    db.flush()
    audit.record(db, event="auto_pay_retired", actor=ACTOR, statement_id=statement_id, run_id=run_id,
                 detail={"reason": reason, "out_entry_id": out_id, "in_entry_id": in_id,
                         "released_statement_ids": sorted(released)})
    return released


def _leg(db: Session, entry_id: int | None) -> LedgerEntry | None:
    return None if entry_id is None else db.get(LedgerEntry, entry_id, populate_existing=True)


def _final(db: Session, row: StatementAutoPay, status: str, detail: dict) -> str:
    row.status = status
    db.flush()
    audit.record(db, event=f"auto_pay_{status}", actor=ACTOR, statement_id=row.statement_id, detail=detail)
    return status


def check_row(db: Session, row: StatementAutoPay, today: date, settings: dict) -> str | None:
    """One housekeeping pass over a booked row (§4.4 step 0); returns the transition applied, or None. In order:
    both legs gone → `owner_deleted`; a leg gone or not `source='statement'` (an owner edit, PUT /transfers) →
    `owner_edited` (figures untouched); a twin within `twin_window_days` of the pay date, looked for until
    `pay_date + twin_window_days + 7` → the pair is retired → `retired` (None when `retire_pair`'s locked
    re-check fails: another write got there first); the legs' amount / date / accounts differ
    from the row → copied onto it (no audit) → `updated`; the statement's total or due date differs from the row →
    both legs `needs_review`, `reason='header_changed'` (once) → `header_changed`. A description, name or category
    change is none of these. Reads without locks (only `retire_pair` locks, then re-checks). Flushes, never
    commits."""
    if row.status != "booked":
        return None
    out_id, in_id = row.out_entry_id, row.in_entry_id
    out_leg, in_leg = _leg(db, out_id), _leg(db, in_id)
    if out_leg is None and in_leg is None:
        return _final(db, row, "owner_deleted", _figures(row))
    if out_leg is None or in_leg is None or STATEMENT_SOURCE != out_leg.source or STATEMENT_SOURCE != in_leg.source:
        return _final(db, row, "owner_edited", {**_figures(row), "out_entry_id": out_id, "in_entry_id": in_id})
    window = int(settings["twin_window_days"])
    if today <= row.pay_date + timedelta(days=window + TWIN_LOOKOUT_EXTRA_DAYS) and find_twins(
            db, card_account_id=row.card_account_id, pay_account_id=row.pay_account_id, amount=row.amount,
            pay_date=row.pay_date, window_days=window, exclude=(out_id, in_id)):
        if retire_pair(db, row, reason="twin", window_days=window) is None:
            return None  # the locked re-check failed: another write got there first; the next run looks again
        return "retired"
    figures = (-out_leg.amount, out_leg.entry_date, out_leg.account_id, in_leg.account_id)
    if figures != (row.amount, row.pay_date, row.pay_account_id, row.card_account_id):
        row.amount, row.pay_date, row.pay_account_id, row.card_account_id = figures
        db.flush()
        return "updated"
    statement = db.get(AccountStatement, row.statement_id, populate_existing=True)
    if (statement.statement_total != row.amount or statement.due_date != row.due_date) \
            and row.reason != HEADER_CHANGED:
        out_leg.needs_review = in_leg.needs_review = True
        row.reason = HEADER_CHANGED
        db.flush()
        audit.record(db, event="auto_pay_header_changed", actor=ACTOR, statement_id=row.statement_id,
                     detail={**_figures(row), "due_date": row.due_date.isoformat(),
                             "statement_total": str(statement.statement_total),
                             "statement_due_date": None if statement.due_date is None
                             else statement.due_date.isoformat()})
        return HEADER_CHANGED
    return None


# --- sweep-time twin retire (§4.5) ----------------------------------------------------------------------------------


def retire_twins_for_statement(db: Session, statement: AccountStatement, accounts: list[int]) -> list[int]:
    """The sweep's half of owner-wins (§4.5), run by `reconcile` before it loads the population: every booked row
    with a leg on `accounts` whose pay date lies within the period ± `matching.population_window_days`, both legs
    still the system's (`source='statement'`; anything else is housekeeping's `owner_edited`), and a twin within
    the settings' `twin_window_days` → `retire_pair(reason="twin")`. A pair another transaction holds
    (`auto_pay_busy`) is logged and left for the next sweep or the job; a row whose locked re-check fails
    (`retire_pair` returns None) is skipped. Returns the retired row ids. Flushes, never commits."""
    from .reconciliation_service import rules_for  # local: the reconciler calls this function

    rules = rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    span = timedelta(days=matching.population_window_days(rules))
    lo, hi = statement.period_start - span, statement.period_end + span
    row_ids = sorted(set(stand_ins.auto_pay_legs(db, accounts).values()))
    if not row_ids:
        return []
    window = int(settings_service.read_auto_pay_settings(db)["twin_window_days"])
    retired = []
    for row_id in row_ids:
        row = db.get(StatementAutoPay, row_id)
        if not lo <= row.pay_date <= hi:
            continue
        legs = [_leg(db, i) for i in (row.out_entry_id, row.in_entry_id)]
        if any(leg is None or leg.source != STATEMENT_SOURCE for leg in legs):
            continue
        if not find_twins(db, card_account_id=row.card_account_id, pay_account_id=row.pay_account_id,
                          amount=row.amount, pay_date=row.pay_date, window_days=window,
                          exclude=[leg.id for leg in legs]):
            continue
        try:
            released = retire_pair(db, row, reason="twin", window_days=window)
        except CodedConflictError as exc:
            if exc.code != PAIR_BUSY:
                raise
            logger.info("auto_pay.sweep_retire_busy", extra={"auto_pay_id": row_id, "statement_id": statement.id})
            continue
        if released is None:  # the locked re-check failed (already retired, or the owner took the legs over)
            continue
        retired.append(row_id)
    return retired
