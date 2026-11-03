"""Bank wins (spec 7, 4.1; plan P12): a card statement's reward lines replace the engine's posted reward amounts.

`prepare` runs inside `reconciliation_service._match_and_record`, in the reconcile transaction, after the population
locks and before `pass_two_holders`. It never commits and never writes coverage itself: it returns a `BankWinsPlan`
whose claims the reconcile writes through `_write_coverage`.

1. **Candidates** (7.1): accruals whose reward account (the rule's `reward_account_id`, else its card) is one of the
   statement's participating accounts and that are (a) `estimated`/`posted` with `post_on` in the period, (b)
   carried: `posted`, unconfirmed, `post_on` before the period and on or after the `period_start` of the account's
   previous statement (its latest non-retired statement ending before this period starts; none → (b) is empty), or
   (c) confirmed or forfeited by this statement (a re-run). None → `prepare` returns None (7.2).
2. **Pulled lines** (7.3, `pulled_filter`): the statement's reward lines without owner coverage, except a line with
   exactly one equal no-accrual (MOZE-era) reward row in the population. When there are reward lines and every one
   stayed in the matcher, the step stops: no early post, no pairing, no claim; only the accruals this statement
   confirmed or forfeited on an earlier run (c) take the assigned-0 outcome of step 6 (fix round 1 ruling), under
   the same locks.
3. **Locks** (P12, controller ruling): the statement row is the reconcile's; then the candidates' source purchases
   `SELECT ... ORDER BY id FOR UPDATE`, then the union of the candidates' windows in ONE `lock_windows`, then the
   reward ledger rows. All three are NOWAIT, as the reconcile's population locks: a held row raises
   `reconcile_busy`, so the reconcile never waits on a row while it holds ledger rows. (The daily job holds a window
   and then inserts a reward row whose foreign key share-locks the purchase; a reconcile holding that purchase and
   waiting on the window would close a cycle.) The candidates are read unlocked first and re-read under the locks;
   one whose window or purchase was not locked is left to the next run.
4. **Early post** (7.4): candidates still `estimated` are posted here (`reward_posting.post_accrual(locked=True)`).
5. **Pairing** (7.5): pulled lines in id order, candidate rows in (`post_on`, id) order; line k gets row k, the last
   line every remaining row; within a line the amount is spread in proportion to the accruals' `amount` (the
   remainder on the line's last row; all 0 → the whole line on the last row). Lines left without a row are
   `surplus_line_ids` (Task 9 gives them their case).
6. **Outcome** (7.6): a row assigned a non-zero amount takes it on its ledger row and becomes `bank_confirmed` by
   this statement; assigned 0, a row whose `post_on` is in or after this period stays `posted` (its ledger amount back
   at the accrual's `amount`, `confirmed_statement_id` cleared), a carried one is `forfeited` with ledger amount 0.
   `reward_accrual.amount` is never changed (4.1). Confirmed and forfeited purchases lose their `reward_posted` flag.
   The windows are then recomputed (a confirmed row uses its ledger amount of the cap, 5.2.2).
7. **Claims** (7.7): one per paired line, `match_rule = reward_engine`, the line's non-zero rows as principals; the
   claimed rows are re-selected FOR UPDATE and returned as `locked_rows` for `_write_coverage`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Collection, Iterable

from psycopg2.errors import LockNotAvailable
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import AccountStatement, LedgerEntry, RewardAccrual, RewardRule, StatementLine
from app.services import coverage_service, reward_engine, reward_posting, settings_service
from app.services.errors import CodedConflictError
from app.services.statements import matching, retired

MATCH_RULE = "reward_engine"  # coverage `match_rule` and representation kind of a bank-wins claim
RECONCILE_BUSY = "reconcile_busy"  # reconciliation_service.RECONCILE_BUSY (not imported: it imports this module)
ESTIMATED, POSTED, CONFIRMED, FORFEITED = "estimated", "posted", "bank_confirmed", "forfeited"
_Q = Decimal("0.0001")
_ZERO = Decimal("0")


@dataclass
class BankWinsPlan:
    pulled_line_ids: set[int] = field(default_factory=set)  # reward lines taken out of the matcher input
    claims: list[matching.Claim] = field(default_factory=list)  # one per paired line, for `_write_coverage`
    surplus_line_ids: list[int] = field(default_factory=list)  # pulled lines left without a row (Task 9: cases)
    early: dict[int, list[int]] = field(default_factory=dict)  # line id → early accrual ids (Task 9)
    locked_rows: dict[int, LedgerEntry] = field(default_factory=dict)  # the claimed reward rows, locked


# --- candidates --------------------------------------------------------------------------------------------------

def previous_statement(db: Session, statement: AccountStatement) -> AccountStatement | None:
    """The account's latest non-retired statement whose period ends before this one starts."""
    return db.scalars(
        select(AccountStatement)
        .where(AccountStatement.account_id == statement.account_id, AccountStatement.id != statement.id,
               AccountStatement.period_end < statement.period_start, ~retired.clause(AccountStatement.id))
        .order_by(AccountStatement.period_end.desc(), AccountStatement.id.desc())
        .limit(1)
    ).first()


def _candidate_query(db: Session, statement: AccountStatement, accounts: Collection[int]):
    start, end = statement.period_start, statement.period_end
    reasons = [
        and_(RewardAccrual.state.in_((ESTIMATED, POSTED)), RewardAccrual.post_on.between(start, end)),  # (a)
        RewardAccrual.confirmed_statement_id == statement.id,  # (c) a re-run
    ]
    previous = previous_statement(db, statement)
    if previous is not None:  # (b) carried from the previous statement's period
        reasons.append(and_(RewardAccrual.state == POSTED, RewardAccrual.confirmed_statement_id.is_(None),
                            RewardAccrual.post_on < start, RewardAccrual.post_on >= previous.period_start))
    reward_account = func.coalesce(RewardRule.reward_account_id, RewardRule.account_id)
    return (select(RewardAccrual).join(RewardRule, RewardRule.id == RewardAccrual.rule_id)
            .where(reward_account.in_(list(accounts)), or_(*reasons)).order_by(RewardAccrual.id))


def candidates(db: Session, statement: AccountStatement, accounts: Collection[int]) -> list[RewardAccrual]:
    """The statement's candidate accruals (7.1), id order, freshly loaded. Unlocked."""
    db.flush()  # populate_existing below must not discard pending changes
    return list(db.scalars(_candidate_query(db, statement, accounts).execution_options(populate_existing=True)))


def _has_candidates(db: Session, statement: AccountStatement, accounts: Collection[int]) -> bool:
    query = _candidate_query(db, statement, accounts).with_only_columns(RewardAccrual.id).limit(1)
    return db.scalar(query) is not None


# --- pulled lines ------------------------------------------------------------------------------------------------

def _moze_flows(db: Session, statement: AccountStatement, accounts: Collection[int]) -> Counter:
    """flow → count of the no-accrual reward rows in reconcile's population (`load_candidates`' gates: participating
    account, statement currency, top level, dated within the population window, not claimed by another statement,
    not held by this statement's owner coverage)."""
    from app.services import reconciliation_service as rec  # it imports this module

    rules = rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    window = timedelta(days=matching.population_window_days(rules))
    _, owned = rec.owned_coverage(db, statement)
    claimed = coverage_service.claimed_entry_ids(db, exclude_statement_id=statement.id) | owned
    day = func.coalesce(LedgerEntry.posted_date, LedgerEntry.entry_date)
    rows = db.execute(
        select(LedgerEntry.id, LedgerEntry.amount)
        .where(LedgerEntry.account_id.in_(list(accounts)), LedgerEntry.currency == statement.currency,
               LedgerEntry.parent_entry_id.is_(None), LedgerEntry.kind == "reward",
               day >= statement.period_start - window, day <= statement.period_end + window,
               ~exists().where(RewardAccrual.ledger_entry_id == LedgerEntry.id))
    ).all()
    return Counter(Decimal(amount).quantize(_Q) for entry_id, amount in rows if entry_id not in claimed)


def pulled_filter(db: Session, statement: AccountStatement, accounts: Collection[int],
                  lines: Iterable[StatementLine]) -> set[int]:
    """The reward lines among `lines` that leave the matcher (7.3), read only; reconcile and the policy validator
    pass the statement's lines without owner coverage. Empty when the candidate set is empty (7.2). A line stays in
    the matcher when exactly one no-accrual reward row in the population has an equal flow. Empty for a statement
    that is not live: bank wins runs on live statements only (fix round 1 ruling)."""
    reward = [line for line in lines if line.line_kind == "reward"]
    if statement.mode != "live" or not reward or not _has_candidates(db, statement, accounts):
        return set()
    flows = _moze_flows(db, statement, accounts)
    return {line.id for line in reward if flows.get(Decimal(line.flow_amount).quantize(_Q), 0) != 1}


# --- locks -------------------------------------------------------------------------------------------------------

def _busy() -> CodedConflictError:
    return CodedConflictError(RECONCILE_BUSY, "a reward purchase, accrual or reward row is locked by another write")


def _lock_tiers(db: Session, accruals: list[RewardAccrual]) -> set[int]:
    """P12 tiers 1-2: the source purchases, then the candidates' windows in ONE `lock_windows`, NOWAIT inside a
    savepoint. Returns the locked purchase ids."""
    purchases = sorted({a.source_entry_id for a in accruals if a.source_entry_id is not None})
    pairs = {(a.rule_id, a.window_start) for a in accruals}
    try:
        with db.begin_nested():
            locked = set(db.scalars(select(LedgerEntry.id).where(LedgerEntry.id.in_(purchases))
                                    .order_by(LedgerEntry.id).with_for_update(nowait=True))) if purchases else set()
            reward_engine.lock_windows(db, pairs, nowait=True)
    except OperationalError as exc:
        if isinstance(exc.orig, LockNotAvailable):
            raise _busy() from exc
        raise
    return locked


def _lock_rows(db: Session, ids: Iterable[int]) -> dict[int, LedgerEntry]:
    """P12 tier 3: reward ledger rows `SELECT ... ORDER BY id FOR UPDATE NOWAIT`, freshly loaded."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    db.flush()
    try:
        with db.begin_nested():
            rows = db.scalars(select(LedgerEntry).where(LedgerEntry.id.in_(wanted)).order_by(LedgerEntry.id)
                              .with_for_update(nowait=True).execution_options(populate_existing=True))
            return {row.id: row for row in rows}
    except OperationalError as exc:
        if isinstance(exc.orig, LockNotAvailable):
            raise _busy() from exc
        raise


# --- pairing -----------------------------------------------------------------------------------------------------

def spread(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """`total` over `weights` in proportion, 4 places, the remainder on the last; all 0 → all on the last."""
    total = Decimal(total).quantize(_Q)
    weights = [Decimal(w) for w in weights]
    whole = sum(weights, _ZERO)
    if whole == 0:
        return [_ZERO] * (len(weights) - 1) + [total]
    shares = [(total * w / whole).quantize(_Q, rounding=ROUND_HALF_UP) for w in weights[:-1]]
    return shares + [total - sum(shares, _ZERO)]


def pair(lines: list[StatementLine], rows: list[RewardAccrual]) -> tuple[dict[int, list[tuple[RewardAccrual, Decimal]]],
                                                                         list[int]]:
    """7.5: line id → [(accrual, assigned)], and the lines left without a row. `lines` in id order, `rows` in
    (`post_on`, id) order."""
    out: dict[int, list[tuple[RewardAccrual, Decimal]]] = {}
    surplus: list[int] = []
    for k, line in enumerate(lines):
        group = rows[k:k + 1] if k < len(lines) - 1 else rows[k:]
        if not group:
            surplus.append(line.id)
            continue
        out[line.id] = list(zip(group, spread(line.flow_amount, [a.amount for a in group])))
    return out, surplus


def _set_amount(row: LedgerEntry, value: Decimal) -> None:
    value = Decimal(value).quantize(_Q)
    if Decimal(row.amount).quantize(_Q) != value:
        row.amount = value


# --- the step ----------------------------------------------------------------------------------------------------

def prepare(db: Session, statement: AccountStatement, accounts: list[int], lines: list[StatementLine],
            manual_line_ids: set[int]) -> BankWinsPlan | None:
    """The bank-wins step (module docstring). `lines`: the statement's current lines; `manual_line_ids`: those with
    owner coverage (never pulled). None when the candidate set is empty (7.2). Raises `reconcile_busy` when a
    purchase, window or reward row is held by another write. Never commits."""
    first = candidates(db, statement, accounts)
    if not first:
        return None
    claimable = [line for line in lines if line.id not in manual_line_ids]
    plan = BankWinsPlan(pulled_line_ids=pulled_filter(db, statement, accounts, claimable))
    # 7.3: every reward line stayed in the matcher → only this statement's earlier outcomes are revisited (assigned 0)
    stopped = any(line.line_kind == "reward" for line in claimable) and not plan.pulled_line_ids
    if stopped:
        first = [a for a in first if a.confirmed_statement_id == statement.id]
        if not first:
            return plan

    purchases = _lock_tiers(db, first)
    held = reward_engine.held_windows(db)
    accruals = [a for a in candidates(db, statement, accounts)
                if (a.rule_id, a.window_start) in held
                and (a.source_entry_id is None or a.source_entry_id in purchases)
                and (not stopped or a.confirmed_statement_id == statement.id)]
    pairs = {(a.rule_id, a.window_start) for a in accruals}

    for accrual in accruals if not stopped else ():  # 7.4: early post, on this session
        if accrual.state == ESTIMATED:
            reward_posting.post_accrual(db, accrual.id, locked=True)

    from app.services import reconciliation_service as rec  # it imports this module

    # a row another statement claims, or this statement's owner coverage holds, is never claimed twice
    taken = coverage_service.claimed_entry_ids(db, exclude_statement_id=statement.id) | rec.owned_coverage(
        db, statement)[1]
    rows = sorted((a for a in accruals if a.ledger_entry_id is not None and a.state in (POSTED, CONFIRMED, FORFEITED)
                   and a.ledger_entry_id not in taken),
                  key=lambda a: (a.post_on is None, a.post_on, a.id))
    ledger = _lock_rows(db, (a.ledger_entry_id for a in rows))
    rows = [a for a in rows if a.ledger_entry_id in ledger]

    pulled = sorted((line for line in claimable if line.id in plan.pulled_line_ids), key=lambda line: line.id)
    paired, plan.surplus_line_ids = pair(pulled, rows)
    assigned = {a.id: value for group in paired.values() for a, value in group}
    settled: list[int] = []  # purchases of the rows confirmed or forfeited
    for accrual in rows:
        row, value = ledger[accrual.ledger_entry_id], assigned.get(accrual.id, _ZERO)
        if value != 0:
            _set_amount(row, value)
            accrual.state, accrual.confirmed_statement_id = CONFIRMED, statement.id
            settled.append(accrual.source_entry_id)
        elif accrual.post_on is not None and accrual.post_on < statement.period_start:  # carried, not credited
            _set_amount(row, _ZERO)
            accrual.state, accrual.confirmed_statement_id = FORFEITED, statement.id
            settled.append(accrual.source_entry_id)
        else:  # the bank may credit it one cycle late
            _set_amount(row, accrual.amount)
            accrual.state, accrual.confirmed_statement_id = POSTED, None
    db.flush()
    reward_engine.clear_reward_flag(db, settled)  # the purchases are held (tier 1)
    reward_engine.recompute_locked(db, pairs)

    claimed_ids = sorted(a.ledger_entry_id for group in paired.values() for a, value in group if value != 0)
    plan.locked_rows = _lock_rows(db, claimed_ids)
    for line_id, group in paired.items():
        members = tuple(matching.Row(a.ledger_entry_id, "principal", value) for a, value in group if value != 0)
        plan.claims.append(matching.Claim(line_id, matching.Representation(
            kind=MATCH_RULE, rows=members, score=Decimal(1), rule=MATCH_RULE, reasons=())))
    return plan
