"""Reward engine: keeps `reward_accrual` rows in step with entries and rules, and allots each window's cap
(spec 4.1, 4.4, 5.1-5.3; plan P2, P3, P12).

Lock contract (P12): the caller locks its ledger rows first; the engine then takes every accrual row it needs in
**one** `SELECT ... ORDER BY id FOR UPDATE` (`lock_windows`) before it writes, and recomputes those windows with
`locked=True`. Nothing here commits; the caller owns the transaction.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from datetime import date
from decimal import Decimal

from psycopg2.errors import LockNotAvailable
from sqlalchemy import delete, event, exists, func, or_, select, tuple_, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..models import Account, EntryRewardRule, LedgerEntry, RewardAccrual, RewardRule
from ..models.statements import AccountStatement, ReconciliationCase
from . import reward_rule_service
from .errors import RETRY, CodedConflictError
from .reward_math import Slot, allot, estimate
from .reward_windows import post_on_for, window_for

FROZEN = ("posted", "bank_confirmed", "forfeited")
REVIEW_REASON = "reward_posted"  # ledger_entry.review_reason (P10): an edit changed a purchase with a frozen accrual
EARLY_HINT = "reward_early"  # the case hint of a reward line credited before the rule's posting date (P5, P6)
SURPLUS_HINT = "reward_surplus"  # the case hint of a reward line bank wins left without an engine row (spec 7.5)
_OPEN_CASE = ("open", "proposed")
_Q = Decimal("0.0001")
_ZERO = Decimal("0")

Pair = tuple[int, date]  # (rule_id, window_start)
_LOCKED_KEY = "reward_engine.locked_windows"  # db.info: the (rule_id, window_start) keys this transaction locked
_HOOKED_KEY = "reward_engine.lock_hooks"


def card_closing_day(db: Session, account: Account) -> int | None:
    """The card's own closing_day, else its combined parent's, else None (calendar month)."""
    if account.closing_day is not None:
        return account.closing_day
    if account.combined_account_id is not None:
        parent = db.get(Account, account.combined_account_id)
        if parent is not None:
            return parent.closing_day
    return None


def _is_placeholder(rule: RewardRule) -> bool:
    """The MOZE placeholder shape (method fixed, fixed_amount 0) accrues nothing until migrated (5.3)."""
    return rule.method == "fixed" and not rule.fixed_amount


def _has_moze_reward(db: Session, purchase_id: int, rule_id: int) -> bool:
    """A reward row for (purchase, rule) with no accrual (MOZE era): no double booking (5.3)."""
    return bool(db.scalar(select(exists().where(
        LedgerEntry.reward_source_entry_id == purchase_id,
        LedgerEntry.reward_rule_id == rule_id,
        ~exists().where(RewardAccrual.ledger_entry_id == LedgerEntry.id),
    ))))


def _is_refund(entry: LedgerEntry) -> bool:
    return entry.kind == "refund"


def _purchase_of(db: Session, entry: LedgerEntry) -> LedgerEntry | None:
    """The purchase whose rules and window an entry accrues under: itself, or the refunded row for a refund
    (None for a refund of nothing)."""
    if not _is_refund(entry):
        return entry
    if entry.refunds_entry_id is None:
        return None
    return db.get(LedgerEntry, entry.refunds_entry_id)


def _target(db: Session, rule: RewardRule, entry: LedgerEntry, purchase: LedgerEntry):
    """(window, post_on, estimate) of `entry`'s accrual under `rule`; `purchase` is `entry` or the refunded row."""
    card = db.get(Account, rule.account_id)
    window = window_for(rule.window, card_closing_day(db, card), purchase.entry_date)
    post_on = post_on_for(rule.posting, rule.delay_days, rule.post_month_offset, rule.post_day, window,
                          entry.entry_date)
    refund_of = Decimal(purchase.amount) if entry is not purchase else None
    value = estimate(method=rule.method, rate=rule.rate, fixed_amount=rule.fixed_amount, unit_spend=rule.unit_spend,
                     txn_rounding=rule.txn_rounding, amount=Decimal(entry.amount), refund_of=refund_of)
    return window, post_on, value


def _desired(db: Session, entry: LedgerEntry) -> dict[int, tuple]:
    """rule_id -> (window, post_on, estimate) for the accruals `entry` should have."""
    purchase = _purchase_of(db, entry)
    if purchase is None:
        return {}
    # populate_existing: a rule loaded before this transaction waited on the purchase row (prepare_entry's
    # check_rules) must not price the accrual on its pre-wait values; a rule PUT that held the row has committed.
    rules = db.scalars(
        select(RewardRule)
        .join(EntryRewardRule, EntryRewardRule.rule_id == RewardRule.id)
        .where(EntryRewardRule.entry_id == purchase.id)
        .order_by(RewardRule.id)
        .execution_options(populate_existing=True)
    ).all()
    out = {}
    for rule in rules:
        if _is_placeholder(rule) or _has_moze_reward(db, purchase.id, rule.id):
            continue
        out[rule.id] = _target(db, rule, entry, purchase)
    return out


def _group_ids(db: Session, rule_id: int) -> list[int]:
    rule = db.get(RewardRule, rule_id)
    return reward_rule_service.group_rule_ids(db, rule) if rule is not None else [rule_id]


def _group_keys(db: Session, pairs: Iterable[Pair]) -> set[Pair]:
    return {(member, start) for rule_id, start in pairs for member in _group_ids(db, rule_id)}


def _forget_locks(session: Session, *_args) -> None:
    session.info.pop(_LOCKED_KEY, None)
    session.info.pop(_RULES_KEY, None)
    session.info.pop(_CARDS_KEY, None)


def _forget_at_end(session: Session, transaction) -> None:
    """The outermost transaction ended (commit or rollback). Not a savepoint release: SQLAlchemy fires
    `after_commit` for a released savepoint too, and the rows locked before it stay locked."""
    if transaction.parent is None:
        _forget_locks(session)


def _hook(db: Session) -> None:
    """The end of the transaction and any rollback (a savepoint's too) forget the recorded locks. A released
    savepoint keeps them (its locks stay held)."""
    if not db.info.get(_HOOKED_KEY):
        event.listen(db, "after_transaction_end", _forget_at_end)
        for name in ("after_rollback", "after_soft_rollback"):
            event.listen(db, name, _forget_locks)
        db.info[_HOOKED_KEY] = True


def _remember_locks(db: Session, keys: set[Pair]) -> None:
    """Record the window keys this transaction holds (until it ends, `_hook`)."""
    _hook(db)
    db.info.setdefault(_LOCKED_KEY, set()).update(keys)


# --- the card and rule tiers (PR #290 re-checks: rule and card writes serialised against accrual pricing) --------
#
# Lock order (plan P12), stated as WHO CAN WAIT, AND ON WHAT:
#   accrual paths:  tier 1 (source rows) -> card rows FOR KEY SHARE (id order) -> rule rows FOR SHARE (id order)
#                   -> tier 2 (ONE window lock) -> tier 3 (reward rows)
#   rule writers:   card rows FOR KEY SHARE NOWAIT -> rule rows FOR UPDATE NOWAIT -> statement rows NOWAIT
#                   -> tier 1 NOWAIT -> tier 2 NOWAIT
#   card writers:   [own account row FOR NO KEY UPDATE, holding nothing else; credit-sharing rows NOWAIT]
#                   -> card rows FOR UPDATE NOWAIT (the card, its old and new parent, the combined children whose
#                   effective closing_day changes) -> then the rule-writer order above (its cards already held)
# Every path that prices an accrual (`sync_entry`, the multi-entry union locks, the post's re-derivation, bank wins)
# takes the cards it prices under (the rules' cards and, for a card without its own closing_day, its effective
# parent) and then the rules (with their shared-cap groups), and re-reads both under the lock before it computes a
# window or an estimate. A card's closing_day / parent and a card's rule set therefore change only under FOR UPDATE
# on that card (closing_day, re-parenting) or under a share lock a card writer's FOR UPDATE conflicts with (a rule
# write), so an accrual is priced on one committed card and rule version and no rule appears on a card while a
# closing_day change of that card is in flight.
# Card rows are taken FOR KEY SHARE: it conflicts with FOR UPDATE (the card writer's explicit lock, a balance
# adjustment's and a reconciliation account-locker's) but not with an ordinary UPDATE of the row (NO KEY UPDATE)
# or an FK check (KEY SHARE), so ordinary account writes neither wait on accrual paths nor make them wait.
# No cycle: card writers and rule writers never wait on a card, rule, statement, source or window lock (all NOWAIT,
# a held row is the retryable 409 `retry`). Accrual paths are the only waiters on these tiers, and they take them in
# the one fixed order above; an accrual path that already holds a window takes cards and rules NOWAIT (`retry`), and
# bank wins takes them NOWAIT (`reconcile_busy`). So every wait edge on these tiers runs from an accrual path to a
# transaction that holds an earlier tier, or to a writer that never waits on one. Waits outside these tiers (FK
# checks, ordinary DML, the dirty-barrier shared lock in the ledger triggers) are not covered by this argument.
# No transaction takes a card or a rule FOR (KEY) SHARE and later FOR UPDATE: that fails closed (RuntimeError).

_RULES_KEY = "reward_engine.locked_rules"  # db.info: rule id -> "update" | "share" held in this transaction
_CARDS_KEY = "reward_engine.locked_cards"  # db.info: account id -> "update" | "share" held in this transaction


def _group_closure(db: Session, rule_ids: Iterable[int]) -> set[int]:
    ids = set(rule_ids)
    if not ids:
        return set()
    caps = set(db.scalars(select(RewardRule.shared_cap_id).where(
        RewardRule.id.in_(sorted(ids)), RewardRule.shared_cap_id.is_not(None))))
    if caps:
        ids |= set(db.scalars(select(RewardRule.id).where(RewardRule.shared_cap_id.in_(caps))))
    return ids


def _held(db: Session, key: str) -> dict[int, str]:
    _hook(db)
    return db.info.setdefault(key, {})


def _lock_cards(db: Session, card_ids: Iterable[int], *, share: bool, nowait: bool) -> list[Account]:
    """These account rows, id order, re-read under the lock (`populate_existing`): FOR KEY SHARE (`share`) or
    FOR UPDATE. Rows already held in a strong enough mode are skipped; FOR UPDATE over a row this transaction holds
    FOR KEY SHARE fails closed (P12: no upgrade)."""
    held = _held(db, _CARDS_KEY)
    need = sorted({c for c in card_ids if c is not None and held.get(c) not in (
        ("update", "share") if share else ("update",))})
    if not need:
        return []
    if not share and any(held.get(c) == "share" for c in need):
        raise RuntimeError(f"card rows {need}: FOR KEY SHARE held in this transaction is never upgraded (P12)")
    query = select(Account).where(Account.id.in_(need)).order_by(Account.id).execution_options(populate_existing=True)
    rows = list(db.scalars(query.with_for_update(read=share, key_share=share, nowait=nowait)))
    for card_id in need:
        held[card_id] = "share" if share else "update"
    return rows


def _share_cards(db: Session, card_ids: Iterable[int], *, nowait: bool) -> None:
    """The cards an accrual is priced under, FOR KEY SHARE in id order: these cards and, for a card without its own
    closing_day, its combined parent (whose closing_day applies, `card_closing_day`). The effective parent is read
    again under the card's lock; one that changed while the lock waited (a card writer committed) is locked too."""
    db.flush()  # populate_existing below must not discard pending changes
    cards = set(card_ids) - {None}
    wanted = cards | set(db.scalars(select(Account.combined_account_id).where(
        Account.id.in_(sorted(cards)), Account.closing_day.is_(None), Account.combined_account_id.is_not(None))))
    while True:
        _lock_cards(db, wanted, share=True, nowait=nowait)
        parents = {card.combined_account_id for card in db.scalars(select(Account).where(
            Account.id.in_(sorted(cards)))) if card.closing_day is None and card.combined_account_id is not None}
        if parents <= wanted:
            return
        wanted |= parents


def _cards_of_rules(db: Session, rule_ids: Iterable[int]) -> set[int]:
    ids = sorted(set(rule_ids))
    return set(db.scalars(select(RewardRule.account_id).where(RewardRule.id.in_(ids)))) if ids else set()


def _lock_rules(db: Session, rule_ids: Iterable[int], *, share: bool, nowait: bool,
                card_ids: Iterable[int] = ()) -> None:
    """The cards first (`_share_cards`: the rules' cards, `card_ids` and their effective parents, FOR KEY SHARE),
    then the rules and their shared-cap groups, in id order, reloaded under the lock; repeats while the group
    closure grows (a group the lock waited on changed). Rows already held in a strong enough mode are skipped. A
    rule an accrual prices under must exist (links and accruals reference rules by RESTRICT foreign keys, so a
    missing one means a concurrent delete): it fails closed with `retry`. A rule writer's missing ids are left to
    its own validation (an unknown `share_cap_with` stays a 422)."""
    db.flush()  # populate_existing below must not discard pending changes
    held = _held(db, _RULES_KEY)
    wanted = _group_closure(db, rule_ids)
    cards = set(card_ids)
    while True:
        cards |= _cards_of_rules(db, wanted)
        _share_cards(db, cards, nowait=nowait)
        need = sorted(r for r in wanted if held.get(r) not in (("update", "share") if share else ("update",)))
        if not need:
            return
        if not share and any(held.get(r) == "share" for r in need):
            raise RuntimeError(f"rule rows {need}: FOR SHARE held in this transaction is never upgraded (P12)")
        query = select(RewardRule).where(RewardRule.id.in_(need)).order_by(RewardRule.id).execution_options(
            populate_existing=True)
        rules = list(db.scalars(query.with_for_update(read=share, nowait=nowait)))
        found = {rule.id for rule in rules}
        if share and found != set(need):
            raise CodedConflictError(RETRY, f"reward rules {sorted(set(need) - found)} no longer exist; try again")
        for rule_id in need:
            held[rule_id] = "share" if share else "update"
        wanted = _group_closure(db, wanted | found)


def lock_cards_for_write(db: Session, card_ids: Iterable[int]) -> None:
    """A card writer's first engine lock (see the lock-order note above): these account rows FOR UPDATE NOWAIT in
    id order, before the rows change. A card an accrual path or a rule write holds (FOR KEY SHARE), or another card
    write, raises the retryable 409 `retry`; it never waits."""
    db.flush()
    with _no_wait(db, "this card is being used by another write; try again"):
        _lock_cards(db, card_ids, share=False, nowait=True)


def lock_other_accounts_nowait(db: Session, account_ids: Iterable[int]) -> None:
    """Account rows a card writer will also UPDATE (credit sharing), FOR NO KEY UPDATE NOWAIT in id order, before
    its card lock: so the card writer's later ordinary DML on them cannot wait while it holds a card. FOR NO KEY
    UPDATE does not conflict with an accrual path's FOR KEY SHARE. A held row is the retryable 409 `retry`."""
    ids = sorted(set(account_ids) - {None})
    if ids:
        with _no_wait(db, "an account of this write is being written; try again"):
            db.execute(select(Account.id).where(Account.id.in_(ids)).order_by(Account.id)
                       .with_for_update(key_share=True, nowait=True)).all()


def lock_rules_for_write(db: Session, rule_ids: Iterable[int], *, card_ids: Iterable[int] = ()) -> None:
    """A rule writer's first locks (see the lock-order note above): the rules' cards and `card_ids` (a POST: the
    new rule's card) with their effective parents FOR KEY SHARE NOWAIT, then these rules and their shared-cap groups
    FOR UPDATE NOWAIT in id order, before the rule rows change and before any other engine lock. A held row (a card
    write in flight, an accrual write pricing under the rule, another rule write) raises the retryable 409 `retry`;
    it never waits."""
    with _no_wait(db, "a reward rule is being used by another write; try again"):
        _lock_rules(db, rule_ids, share=False, nowait=True, card_ids=card_ids)


def share_rules(db: Session, rule_ids: Iterable[int], *, nowait: bool = False) -> None:
    """An accrual writer's card and rule tiers (see the note above): the rules' cards (with their effective parents)
    FOR KEY SHARE, then the rules and their groups FOR SHARE, in id order, after its tier-1 rows and before it prices
    or locks a window; cards and rules are re-read under the lock. Never waits on a card or a rule while this
    transaction holds a window: then the locks are NOWAIT and a held row raises the retryable 409 `retry`.
    `nowait=True` (bank wins) raises the driver's LockNotAvailable for the caller's `reconcile_busy`."""
    if nowait:
        _lock_rules(db, rule_ids, share=True, nowait=True)
    elif held_windows(db):
        with _no_wait(db, "a reward rule or card is being written; try again"):
            _lock_rules(db, rule_ids, share=True, nowait=True)
    else:
        _lock_rules(db, rule_ids, share=True, nowait=False)


def share_entry_rules(db: Session, entry_ids: Iterable[int], extra_rule_ids: Iterable[int] = ()) -> None:
    """`share_rules` over every rule these entries price under: their links (a refund: its purchase's), the rules of
    their current accruals and of their refunds' and children's, and `extra_rule_ids` (a prepared write's links)."""
    ids = set(entry_ids)
    rules = set(extra_rule_ids)
    if ids:
        purchases = set(db.scalars(select(LedgerEntry.refunds_entry_id).where(
            LedgerEntry.id.in_(sorted(ids)), LedgerEntry.refunds_entry_id.is_not(None))))
        related = set(db.scalars(select(LedgerEntry.id).where(or_(
            LedgerEntry.parent_entry_id.in_(sorted(ids)), LedgerEntry.refunds_entry_id.in_(sorted(ids))))))
        rules |= set(db.scalars(select(EntryRewardRule.rule_id).where(
            EntryRewardRule.entry_id.in_(sorted(ids | purchases)))))
        rules |= set(db.scalars(select(RewardAccrual.rule_id).where(
            RewardAccrual.source_entry_id.in_(sorted(ids | related)))))
    if rules:
        share_rules(db, rules)


class _no_wait:
    """A savepoint around NOWAIT locks: LockNotAvailable becomes the retryable 409 `retry`."""

    def __init__(self, db: Session, message: str):
        self.db, self.message, self.nested = db, message, None

    def __enter__(self):
        self.nested = self.db.begin_nested()
        return self

    def __exit__(self, kind, exc, tb):
        if kind is None:
            self.nested.commit()
            return False
        self.nested.rollback()
        if isinstance(exc, OperationalError) and isinstance(exc.orig, LockNotAvailable):
            raise CodedConflictError(RETRY, self.message) from exc
        return False


def _window_rows(db: Session, pairs: Iterable[Pair], *, lock: bool, nowait: bool = False) -> list[RewardAccrual]:
    db.flush()  # populate_existing below must not discard pending changes
    keys = _group_keys(db, pairs)
    if not keys:
        return []
    query = (
        select(RewardAccrual)
        .where(tuple_(RewardAccrual.rule_id, RewardAccrual.window_start).in_(sorted(keys)))
        .order_by(RewardAccrual.id)
        .execution_options(populate_existing=True)
    )
    if not lock or keys <= db.info.get(_LOCKED_KEY, set()):
        # A window this transaction already holds is read, not locked again: a multi-entry write takes its whole
        # union in one `lock_windows` first (P12), and the per-entry calls after it must issue no second lock.
        return list(db.scalars(query))
    rows = list(db.scalars(query.with_for_update(nowait=nowait)))
    _remember_locks(db, keys)  # recorded even for windows with no rows yet
    return rows


def window_rows(db: Session, rule_id: int, window_start: date) -> list[RewardAccrual]:
    """Every accrual of one window of the rule's shared-cap group, read without locks (the caller holds them, or only
    previews)."""
    return _window_rows(db, [(rule_id, window_start)], lock=False)


def desired_accruals(db: Session, entry: LedgerEntry) -> dict[int, tuple]:
    """rule_id -> (window, post_on, estimate) for the accruals `entry` should have on its current figures."""
    return _desired(db, entry)


def lock_windows(db: Session, pairs: Iterable[Pair], *, nowait: bool = False) -> list[RewardAccrual]:
    """Lock every accrual of these windows (each rule's whole shared-cap group) in one
    `SELECT ... ORDER BY id FOR UPDATE` and return them, freshly loaded, in id order (P12 step 2). The
    group-expanded (rule_id, window_start) keys are recorded in `db.info` until the transaction ends, so
    `recompute_window(locked=True)` can check that its window is held. Keys all held already issue no lock
    statement (the rows are read fresh). `nowait=True` (reconcile, which never
    waits on a row while it holds ledger rows: bank wins) raises the driver's LockNotAvailable on a held row."""
    return _window_rows(db, pairs, lock=True, nowait=nowait)


def held_windows(db: Session) -> frozenset[Pair]:
    """The (rule_id, window_start) keys `lock_windows` holds in this transaction (group-expanded)."""
    return frozenset(db.info.get(_LOCKED_KEY, ()))


def require_window_locked(db: Session, rule_id: int, window_start: date, *, group: list[int] | None = None) -> None:
    """Raise RuntimeError unless `lock_windows` covered this window of the rule's whole shared-cap group in this
    transaction (the `locked=True` contract, P12)."""
    if group is None:
        group = _group_ids(db, rule_id)
    missing = {(member, window_start) for member in group} - db.info.get(_LOCKED_KEY, set())
    if missing:
        raise RuntimeError(
            f"locked=True for rule {rule_id} window {window_start}: this transaction holds no lock_windows lock on "
            f"{sorted(missing)}")


def _frozen_amounts(db: Session, rows: list[RewardAccrual]) -> dict[int, Decimal]:
    """Cap use of frozen accruals (5.2.2): posted -> its amount; bank_confirmed / forfeited -> the ledger row's
    amount, 0 without a row."""
    ledger_ids = [r.ledger_entry_id for r in rows
                  if r.state in ("bank_confirmed", "forfeited") and r.ledger_entry_id is not None]
    ledger = dict(db.execute(
        select(LedgerEntry.id, LedgerEntry.amount).where(LedgerEntry.id.in_(ledger_ids))
    ).all()) if ledger_ids else {}
    out = {}
    for r in rows:
        if r.state == "posted":
            out[r.id] = Decimal(r.amount)
        elif r.state in ("bank_confirmed", "forfeited"):
            out[r.id] = Decimal(ledger.get(r.ledger_entry_id, _ZERO)) if r.ledger_entry_id else _ZERO
    return out


def _external_used(db: Session, rule_ids: list[int], start: date, end: date) -> Decimal:
    """No-accrual (MOZE era) reward rows of the group whose source purchase is in the window (5.3)."""
    source = LedgerEntry.__table__.alias("source")
    total = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(source, source.c.id == LedgerEntry.reward_source_entry_id)
        .where(
            LedgerEntry.reward_rule_id.in_(rule_ids),
            source.c.entry_date.between(start, end),
            ~exists().where(RewardAccrual.ledger_entry_id == LedgerEntry.id),
        )
    )
    return Decimal(total)


def recompute_window(db: Session, rule_id: int, window_start: date, *, locked: bool = False) -> None:
    """Allot the cap of one window of a rule's shared-cap group to its estimated accruals (5.2).

    `locked=False` locks the window's accruals itself (`lock_windows`); `locked=True` takes no locks, the caller
    already holds them (P12)."""
    rule = db.get(RewardRule, rule_id)
    if rule is None:
        return
    group = reward_rule_service.group_rule_ids(db, rule)
    if locked:
        require_window_locked(db, rule_id, window_start, group=group)
    rows = _window_rows(db, [(rule_id, window_start)], lock=not locked)
    estimated = [r for r in rows if r.state == "estimated"]
    if not estimated:
        return  # frozen amounts are never rewritten; no estimated row, no adjustment (5.2.4)
    amounts = window_amounts(db, rule, group, window_start, rows)
    for r in estimated:
        value = amounts[r.id]
        if Decimal(r.amount) != value:
            r.amount = value
    db.flush()


def window_amounts(db: Session, rule: RewardRule, group: list[int], window_start: date, rows: list[RewardAccrual],
                   unfreeze: Mapping[int, Decimal] | None = None) -> dict[int, Decimal]:
    """The allotment (5.2) of one window's accrual `rows` (all of them, the caller holds the window locks): accrual
    id -> amount, quantized, for every estimated row and every row in `unfreeze`. `unfreeze` (reprice, issue #306):
    frozen accrual id -> the estimate to price it on, allotted as if it were still estimated; other frozen rows keep
    their cap use. Writes nothing."""
    unfreeze = unfreeze or {}
    open_rows = [r for r in rows if r.state == "estimated" or r.id in unfreeze]
    if not open_rows:
        return {}
    window_end = max(r.window_end for r in open_rows)
    dates = dict(db.execute(
        select(LedgerEntry.id, LedgerEntry.entry_date)
        .where(LedgerEntry.id.in_(sorted({r.source_entry_id for r in rows if r.source_entry_id is not None})))
    ).all())
    frozen = {k: v for k, v in _frozen_amounts(db, rows).items() if k not in unfreeze}
    ordered = sorted(rows, key=lambda r: (dates.get(r.source_entry_id, date.max), r.id))
    slots = [Slot(key=r.id, estimate=Decimal(unfreeze.get(r.id, r.estimate)), frozen=frozen.get(r.id))
             for r in ordered]
    # The group's lowest-id rule carries the window's total_rounding, so every member computes the same split.
    lead = db.get(RewardRule, group[0])
    amounts = allot(slots, cap=reward_rule_service.group_cap(db, rule),
                    external_used=_external_used(db, group, window_start, window_end),
                    total_rounding=lead.total_rounding)
    return {r.id: amounts[r.id].quantize(_Q) for r in open_rows}


def _recompute_all(db: Session, pairs: Iterable[Pair]) -> None:
    for rule_id, start in sorted(set(pairs)):
        recompute_window(db, rule_id, start, locked=True)


def recompute_locked(db: Session, pairs: Iterable[Pair]) -> None:
    """`recompute_window(..., locked=True)` over these windows; the caller took them with `lock_windows` (or
    `delete_estimated_for`) earlier in this transaction."""
    _recompute_all(db, pairs)


def _set(row: RewardAccrual, window, post_on, value: Decimal) -> None:
    row.window_start, row.window_end = window
    row.post_on = post_on
    row.estimate = value


def sync_entry(db: Session, entry_id: int) -> None:
    """Create, re-bucket or delete `entry_id`'s estimated accruals to match its rule links (a refund: its
    purchase's links, in the purchase's window), then recompute every window touched, old and new. Frozen
    accruals are never changed. A purchase brings its refunds along (same links, date and amount base); the
    purchase's and its refunds' windows are locked in one `lock_windows` (P12)."""
    db.flush()  # _desired reloads rule rows (populate_existing) and must not discard pending changes
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:
        return
    entries = [entry]
    if not _is_refund(entry):
        entries += list(db.scalars(
            select(LedgerEntry).where(LedgerEntry.refunds_entry_id == entry_id).order_by(LedgerEntry.id)))
    share_entry_rules(db, [e.id for e in entries])  # rule tier: priced on committed rules (P12)
    desired = {e.id: _desired(db, e) for e in entries}
    current = _accruals_of(db, desired)
    pairs = {(a.rule_id, a.window_start) for a in current}
    pairs |= {(rule_id, target[0][0]) for wanted in desired.values() for rule_id, target in wanted.items()}
    locked = {a.id: a for a in lock_windows(db, pairs)}
    have: set[tuple[int, int]] = set()
    for accrual in (locked.get(a.id, a) for a in current):
        if accrual.source_entry_id not in desired:
            continue  # defensive: populate_existing reloaded a row whose source is no longer one of ours
        wanted = desired[accrual.source_entry_id]
        have.add((accrual.source_entry_id, accrual.rule_id))
        if accrual.state in FROZEN:
            continue
        if accrual.rule_id not in wanted:
            db.delete(accrual)
        else:
            _set(accrual, *wanted[accrual.rule_id])
    for source_id, wanted in desired.items():
        for rule_id, (window, post_on, value) in wanted.items():
            if (source_id, rule_id) in have:
                continue
            db.add(RewardAccrual(source_entry_id=source_id, rule_id=rule_id, window_start=window[0],
                                 window_end=window[1], post_on=post_on, estimate=value, amount=value))
    db.flush()
    _recompute_all(db, pairs)


def _accruals_of(db: Session, entry_ids: Iterable[int]) -> list[RewardAccrual]:
    db.flush()  # populate_existing below must not discard pending changes (sessions run autoflush=False)
    ids = list(entry_ids)
    if not ids:
        return []
    return list(db.scalars(
        select(RewardAccrual).where(RewardAccrual.source_entry_id.in_(ids)).order_by(RewardAccrual.id)
        .execution_options(populate_existing=True)
    ))


def _estimated_of_rules(db: Session, rule_ids: list[int]) -> list[RewardAccrual]:
    db.flush()  # populate_existing below must not discard pending changes
    return list(db.scalars(
        select(RewardAccrual)
        .where(RewardAccrual.rule_id.in_(rule_ids), RewardAccrual.state == "estimated")
        .order_by(RewardAccrual.id)
        .execution_options(populate_existing=True)
    ))


def _lock_sources(db: Session, rule_ids: list[int]) -> list[RewardAccrual]:
    """P12 tier 1 of a rule or card write: every source row of the rules' estimated accruals, and for a refund also
    the purchase it refunds (its window and amount base), `SELECT ... ORDER BY id FOR UPDATE`, reloaded. A purchase
    write in flight holds its row until it commits, so the accruals re-read after the lock are the committed ones
    and nothing is priced on a value read before the wait. Returns the re-read accruals.

    Every batch is locked NOWAIT (a rule writer never waits, see the rule-tier note): a held row raises the
    retryable 409 `retry` and the caller's transaction rolls back. The loop re-reads until the source set no longer
    grows (with the rule rows held FOR UPDATE no new accrual of these rules can appear; the loop stays as a guard)."""
    held: set[int] = set()
    while True:
        estimated = _estimated_of_rules(db, rule_ids)
        sources = {a.source_entry_id for a in estimated if a.source_entry_id is not None}
        refunded = set(db.scalars(select(LedgerEntry.refunds_entry_id).where(
            LedgerEntry.id.in_(sorted(sources)), LedgerEntry.kind == "refund",
            LedgerEntry.refunds_entry_id.is_not(None)))) if sources else set()
        need = sorted((sources | refunded) - held)
        if not need:
            return estimated
        query = (select(LedgerEntry).where(LedgerEntry.id.in_(need)).order_by(LedgerEntry.id)
                 .with_for_update(nowait=True).execution_options(populate_existing=True))
        with _no_wait(db, "a reward purchase linked to this rule is being written; try again"):
            db.execute(query).all()
        held.update(need)


def recompute_rules(db: Session, rule_ids: Iterable[int]) -> None:
    """After a rule or card write: re-bucket and re-estimate every estimated accrual of these rules, then
    recompute their old and new windows. Frozen accruals keep their window and amount (5.1).

    P12: the caller holds the rule rows (`lock_rules_for_write`); then the accruals' source rows (tier 1,
    `_lock_sources`, NOWAIT), the plans built from their values re-read under those locks, then ONE NOWAIT
    `lock_windows` over the old and new windows (tier 2). The caller holds no window lock yet; a write that touches
    several cards passes all their rules in one call (`recompute_accounts`)."""
    ids = sorted(set(rule_ids))
    if not ids:
        return
    estimated = _lock_sources(db, ids)
    plans: dict[int, tuple] = {}
    for accrual in estimated:
        if accrual.source_entry_id is None:
            continue
        entry = db.get(LedgerEntry, accrual.source_entry_id)
        purchase = _purchase_of(db, entry) if entry is not None else None
        if purchase is None:
            continue
        plans[accrual.id] = _target(db, db.get(RewardRule, accrual.rule_id), entry, purchase)
    pairs = {(a.rule_id, a.window_start) for a in estimated}
    pairs |= {(a.rule_id, plans[a.id][0][0]) for a in estimated if a.id in plans}
    with _no_wait(db, "a reward window of this rule is being written; try again"):
        rows = {a.id: a for a in lock_windows(db, pairs, nowait=True)}
    for accrual_id, plan in plans.items():
        row = rows.get(accrual_id)
        if row is not None and row.state == "estimated":
            _set(row, *plan)
    db.flush()
    _recompute_all(db, pairs)


def rederive(db: Session, accrual: RewardAccrual) -> bool:
    """Re-price an estimated accrual on the committed rule and purchase, under locks the caller holds (its source
    row, P12 tier 1, and its window, tier 2): the posting step's guard against an estimate priced on a value that
    changed after it was computed (a new purchase priced on a rule a concurrent rule PUT was rewriting). True: same
    window and post_on, the estimate refreshed (the caller recomputes the window). False: the accrual no longer
    matches its source (another window or post_on, or the link is gone): not postable under these locks."""
    if accrual.state != "estimated" or accrual.source_entry_id is None:
        return True
    db.flush()  # populate_existing below must not discard pending changes
    entry = db.get(LedgerEntry, accrual.source_entry_id, populate_existing=True)
    if entry is None:
        return False
    if _is_refund(entry) and entry.refunds_entry_id is not None:
        db.get(LedgerEntry, entry.refunds_entry_id, populate_existing=True)
    target = _desired(db, entry).get(accrual.rule_id)
    if target is None:
        return False
    window, post_on, value = target
    if tuple(window) != (accrual.window_start, accrual.window_end) or post_on != accrual.post_on:
        return False
    if Decimal(accrual.estimate) != value:
        accrual.estimate = value
        db.flush()
    return True


def recompute_account(db: Session, account_id: int) -> None:
    """`recompute_accounts` of one card."""
    recompute_accounts(db, [account_id])


def recompute_accounts(db: Session, account_ids: Iterable[int]) -> None:
    """`recompute_rules` over these cards' rules in ONE call (a closing_day change: the card and the children whose
    closing day resolves through it), so the sources of every card are locked before any window (P12)."""
    ids = sorted(set(account_ids))
    if not ids:
        return
    lock_cards_for_write(db, ids)  # a card write: held already when the caller locked its card set (P12 card tier)
    recompute_rules(db, db.scalars(select(RewardRule.id).where(RewardRule.account_id.in_(ids))))


def planned_windows(db: Session, rule_ids: Iterable[int], entry_date: date) -> set[Pair]:
    """The (rule_id, window_start) keys an entry dated `entry_date` and linked to these rules accrues in: the new
    windows `sync_entry` will lock for a prepared insert or update (a superset: placeholders included)."""
    ids = sorted(set(rule_ids))
    if not ids:
        return set()
    out = set()
    for rule in db.scalars(select(RewardRule).where(RewardRule.id.in_(ids)).order_by(RewardRule.id)):
        card = db.get(Account, rule.account_id)
        out.add((rule.id, window_for(rule.window, card_closing_day(db, card), entry_date)[0]))
    return out


def lock_entry_windows(db: Session, entry_ids: Iterable[int], planned: Iterable[Pair] = ()) -> None:
    """P12 tier 2 of a multi-entry write (a split): ONE `lock_windows` over the current windows of these entries'
    accruals, of their fee / discount children's and of their refunds' (any state), plus the `planned` new windows.
    Every `sync_entry` / `drop_for_delete` the write runs afterwards then finds its windows held and issues no
    further lock; two writes on one card can no longer each hold one window and wait on the other. The caller
    holds its tier-1 rows (entries, and the refunds of entries it deletes: `lock_refunds_of`) already."""
    ids = sorted(set(entry_ids))
    pairs = set(planned)
    if ids:
        related = set(db.scalars(select(LedgerEntry.id).where(or_(
            LedgerEntry.parent_entry_id.in_(ids), LedgerEntry.refunds_entry_id.in_(ids)))))
        pairs |= {(a.rule_id, a.window_start) for a in _accruals_of(db, sorted(set(ids) | related))}
    if pairs:
        lock_windows(db, pairs)


def lock_written_entries(db: Session, entries: Iterable[LedgerEntry]) -> None:
    """`lock_entry_windows` for a multi-entry write whose rows and rule links are already written (flushed or about
    to be) but not yet synced (both legs of a transfer): the new windows are the ones `sync_entry` will want
    (`_desired`; a purchase's refunds share its windows). Call it before the first `after_write` of the write."""
    rows = [entry for entry in entries if entry is not None]
    db.flush()
    share_entry_rules(db, [entry.id for entry in rows])  # rule tier before pricing (P12)
    planned = {(rule_id, target[0][0]) for entry in rows for rule_id, target in _desired(db, entry).items()}
    lock_entry_windows(db, [entry.id for entry in rows], planned)


def lock_refunds_of(db: Session, entry_ids: Collection[int]) -> list[int]:
    """The refunds that point at these entries (and are not among them), `SELECT ... ORDER BY id FOR UPDATE`
    (P12 tier 1 of a delete, before any accrual lock)."""
    ids = sorted(set(entry_ids))
    if not ids:
        return []
    return list(db.scalars(select(LedgerEntry.id).where(
        LedgerEntry.refunds_entry_id.in_(ids), LedgerEntry.id.not_in(ids)).order_by(LedgerEntry.id).with_for_update()))


def delete_estimated_for(db: Session, entry_ids: Iterable[int]) -> set[Pair]:
    """Delete the estimated accruals of these source entries (their windows locked first, P12) and return the
    (rule_id, window_start) pairs to recompute once the entries are gone. Frozen accruals stay."""
    current = _accruals_of(db, entry_ids)
    pairs = {(a.rule_id, a.window_start) for a in current if a.state == "estimated"}
    if not pairs:
        return set()
    ids = {a.id for a in current}
    doomed = [a.id for a in lock_windows(db, pairs) if a.id in ids and a.state == "estimated"]
    if doomed:
        db.execute(delete(RewardAccrual).where(RewardAccrual.id.in_(doomed)).execution_options(
            synchronize_session="fetch"))
    db.flush()
    return pairs


# --- write-path hooks (Task 6: spec 5.1, 5.4; plan P1, P6, P10, P12) ---------------------------------------------


def financial_key(entry: LedgerEntry, rule_ids: Collection[int]) -> tuple:
    """What a frozen accrual was priced on (5.1): (amount, entry_date, account_id, sorted rule ids)."""
    return (Decimal(entry.amount), entry.entry_date, entry.account_id, tuple(sorted(set(rule_ids))))


def _linked_rule_ids(db: Session, entry_id: int) -> list[int]:
    return list(db.scalars(select(EntryRewardRule.rule_id).where(EntryRewardRule.entry_id == entry_id)))


def _has_frozen(db: Session, entry_id: int) -> bool:
    """A frozen accrual with a reward row: only that row can be reviewed (a forfeited accrual without one has
    nothing the flag could point at, and nothing could clear it)."""
    return bool(db.scalar(select(exists().where(
        RewardAccrual.source_entry_id == entry_id, RewardAccrual.state.in_(FROZEN),
        RewardAccrual.ledger_entry_id.is_not(None)))))


def _settle_flag(db: Session, entry: LedgerEntry, *, changed: bool, before_flag: tuple[bool, str | None]) -> None:
    """After `_apply` cleared the flag: set it when a financial edit hit a frozen accrual; otherwise restore a prior
    `reward_posted` flag (a non-financial edit keeps it, 5.1)."""
    if changed and _has_frozen(db, entry.id):
        entry.needs_review, entry.review_reason = True, REVIEW_REASON
    elif not changed and before_flag[1] == REVIEW_REASON:
        entry.needs_review, entry.review_reason = before_flag


def move_reward_projects(db: Session, entry: LedgerEntry, before_project_id: int | None) -> None:
    """A purchase's project change moves its engine reward rows whose project is null or the old one (5.1).
    Estimated rewards follow by themselves (the posting reads the purchase's project); MOZE rows stay history."""
    if entry.project_id == before_project_id:
        return
    reward_ids = select(RewardAccrual.ledger_entry_id).where(
        RewardAccrual.source_entry_id == entry.id, RewardAccrual.ledger_entry_id.is_not(None))
    db.execute(
        update(LedgerEntry)
        .where(LedgerEntry.id.in_(reward_ids),
               or_(LedgerEntry.project_id.is_(None), LedgerEntry.project_id == before_project_id))
        .values(project_id=entry.project_id)
        .execution_options(synchronize_session="fetch")
    )


def after_write(db: Session, entry: LedgerEntry, *, before_key: tuple | None, before_project_id: int | None,
                before_flag: tuple[bool, str | None]) -> None:
    """Called after `_apply` and `write_rule_links` (insert: `before_key=None`). Syncs the entry's accruals, then
    sets or restores the `reward_posted` flag and moves reward projects (5.1)."""
    sync_entry(db, entry.id)
    if before_key is None:
        return
    changed = financial_key(entry, _linked_rule_ids(db, entry.id)) != before_key
    _settle_flag(db, entry, changed=changed, before_flag=before_flag)
    move_reward_projects(db, entry, before_project_id)
    db.flush()


def capture(db: Session, entry: LedgerEntry) -> dict:
    """The before-state `after_write` needs, read before an action rewrites the row's amount, date or account."""
    return {"before_key": financial_key(entry, _linked_rule_ids(db, entry.id)),
            "before_project_id": entry.project_id, "before_flag": (entry.needs_review, entry.review_reason)}


def after_write_captured(db: Session, entry: LedgerEntry, before: dict) -> None:
    after_write(db, entry, **before)


def after_meta_write(db: Session, entry: LedgerEntry, *, before_project_id: int | None,
                     before_flag: tuple[bool, str | None]) -> None:
    """A metadata-only edit (split keep_full meta, keep_meta): nothing to sync; restore the flag, move projects."""
    _settle_flag(db, entry, changed=False, before_flag=before_flag)
    move_reward_projects(db, entry, before_project_id)
    db.flush()


def mark_early_rechecks(db: Session, account_id: int) -> None:
    """P6: the card's statements holding an open `reward_early` case get `needs_recheck`, so the re-run supersedes
    the case. Call it before any accrual lock (the statement row comes first in the P12 order)."""
    flagged = (select(AccountStatement.id).where(AccountStatement.account_id == account_id, exists().where(
        ReconciliationCase.statement_id == AccountStatement.id,
        ReconciliationCase.status.in_(_OPEN_CASE),
        ReconciliationCase.context["hint"].astext == EARLY_HINT,
    )).order_by(AccountStatement.id))
    with _no_wait(db, "a statement of this card is being reconciled; try again"):  # a rule writer never waits
        ids = list(db.scalars(flagged.with_for_update(nowait=True)))
    if ids:
        db.execute(update(AccountStatement).where(AccountStatement.id.in_(ids)).values(needs_recheck=True)
                   .execution_options(synchronize_session=False))


def drop_for_delete(db: Session, entry_ids: Collection[int]) -> set[Pair]:
    """The delete path's engine step (5.4), before the entries go: lock the refunds that point at the entries
    (`SELECT ... ORDER BY id FOR UPDATE`, P12 tier 1, before any accrual lock: the reward job locks a refund, its
    accrual's source, before the window, and the cascade nulls their `refunds_entry_id`; already held when the caller
    folded them into its own tier-1 lock), delete the estimated accruals of the entries and of those refunds
    (refunds are kept, unlinked, and accrue nothing without their purchase), then flag the surviving frozen
    accruals' reward rows `reward_posted`. Returns the windows to `recompute_locked` once the rows are deleted."""
    ids = sorted(set(entry_ids))
    if not ids:
        return set()
    refunds = lock_refunds_of(db, ids)
    pairs = delete_estimated_for(db, ids + refunds)
    rewards = select(RewardAccrual.ledger_entry_id).where(
        RewardAccrual.source_entry_id.in_(ids), RewardAccrual.state.in_(FROZEN),
        RewardAccrual.ledger_entry_id.is_not(None))
    db.execute(
        update(LedgerEntry)
        .where(LedgerEntry.id.in_(rewards), LedgerEntry.id.not_in(ids))
        .values(needs_review=True, review_reason=REVIEW_REASON)
        .execution_options(synchronize_session=False)
    )
    return pairs


def prepare_reward_delete(db: Session, reward_entry_id: int) -> RewardAccrual | None:
    """The owner deletes an engine reward row (5.4, P1), in the P12 order, after `lock_group` and before
    `locked_with_legs`: the source purchase `SELECT ... FOR UPDATE` (tier 1) and its `reward_posted` flag cleared
    (controller ruling: as a bank-wins forfeit), then the accrual window (tier 2, `lock_reward_accrual`). The
    caller forfeits the returned accrual later; any refusal after this point rolls the clear back with the
    transaction. None for a reward row without an accrual (MOZE era)."""
    found = db.execute(
        select(RewardAccrual.source_entry_id).where(RewardAccrual.ledger_entry_id == reward_entry_id)
    ).first()
    if found is None:
        return None
    if found.source_entry_id is not None:
        db.execute(select(LedgerEntry.id).where(LedgerEntry.id == found.source_entry_id).with_for_update())
        clear_reward_flag(db, [found.source_entry_id])
    return lock_reward_accrual(db, reward_entry_id)


def lock_reward_accrual(db: Session, reward_entry_id: int) -> RewardAccrual | None:
    """An engine reward row's accrual, its window (group) locked in ONE `SELECT ... ORDER BY id FOR UPDATE` (P12:
    after the group lock, before the ledger rows). None for a reward row without an accrual (MOZE era)."""
    found = db.execute(
        select(RewardAccrual.rule_id, RewardAccrual.window_start)
        .where(RewardAccrual.ledger_entry_id == reward_entry_id)
    ).first()
    if found is None:
        return None
    rows = lock_windows(db, [(found.rule_id, found.window_start)])
    return next((row for row in rows if row.ledger_entry_id == reward_entry_id), None)


def clear_reward_flag(db: Session, entry_ids: Collection[int]) -> None:
    """Clear `needs_review` on these purchases only where its reason is `reward_posted` (an owner forfeit here,
    a bank-wins confirm or forfeit); a flag set for another reason stays. The caller already holds these purchase
    rows (P12 tier 1); it issues no new lock."""
    ids = sorted({i for i in entry_ids if i is not None})
    if not ids:
        return
    db.execute(
        update(LedgerEntry)
        .where(LedgerEntry.id.in_(ids), LedgerEntry.review_reason == REVIEW_REASON)
        .values(needs_review=False, review_reason=None)
        .execution_options(synchronize_session="fetch")
    )


def forfeit_for_delete(db: Session, accrual: RewardAccrual) -> Pair:
    """The owner deleted the accrual's reward row (5.4, P1): `forfeited`, amount kept for the record, no ledger row
    (so it uses 0 of the cap). The caller took the purchase and the window locks and cleared the purchase's flag
    (`prepare_reward_delete`); returns the window to `recompute_locked` after the row is gone."""
    accrual.state, accrual.ledger_entry_id = "forfeited", None
    db.flush()
    return accrual.rule_id, accrual.window_start


def usage(db: Session, account_id: int, on: date) -> list[dict]:
    """Cap use of each in-date enabled rule's window containing `on` (spec 6); a shared-cap group appears once.

    Read-only: `used` and the cap come from the same helpers `recompute_window` allots with."""
    account = db.get(Account, account_id)
    if account is None:
        return []
    closing_day = card_closing_day(db, account)
    rules = db.scalars(
        select(RewardRule)
        .where(RewardRule.account_id == account_id, RewardRule.is_enabled.is_(True),
               or_(RewardRule.starts_on.is_(None), RewardRule.starts_on <= on),
               or_(RewardRule.ends_on.is_(None), RewardRule.ends_on >= on))
        .order_by(RewardRule.sort_order, RewardRule.id)
    ).all()
    out, seen = [], set()
    for rule in rules:
        if rule.shared_cap_id is not None:
            if rule.shared_cap_id in seen:
                continue
            seen.add(rule.shared_cap_id)
        group = reward_rule_service.group_rule_ids(db, rule)
        start, end = window_for(rule.window, closing_day, on)
        rows = _window_rows(db, [(rule.id, start)], lock=False)
        frozen = _frozen_amounts(db, rows)
        used = sum(frozen.values(), _ZERO) + _external_used(db, group, start, end)
        estimated = sum((Decimal(r.amount) for r in rows if r.state == "estimated"), _ZERO)
        cap = reward_rule_service.group_cap(db, rule)
        out.append({
            "rule_ids": group, "shared_cap_id": rule.shared_cap_id, "name": rule.name,
            "window_start": start, "window_end": end,
            "used": used.quantize(_Q), "estimated": estimated.quantize(_Q),
            "cap": cap, "remaining": None if cap is None else max(_ZERO, Decimal(cap) - used - estimated).quantize(_Q),
        })
    return out
