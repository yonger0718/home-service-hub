"""Reward rule writes: create and update under /accounts/{id}/reward-rules (reward rules design §5)."""
import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Account, EntryRewardRule, LedgerEntry, Project, RewardRule
from ..schemas.writes import RewardRuleIn, ShareCapWith
from . import ledger_service
from .errors import CodedConflictError, NotFoundError, ValidationError

BASIC_RULE_OVERLAP = "basic_rule_overlap"
RULE_REFERENCED = "rule_referenced"

# Columns copied from the payload verbatim; sort_order and share_cap_with have their own handling.
_WRITE_FIELDS = (
    "name", "method", "rate", "fixed_amount", "unit_spend", "window", "posting", "delay_days", "post_month_offset", "post_day",
    "txn_rounding", "total_rounding", "total_cap", "is_basic", "reward_account_id", "reward_project_id",
    "starts_on", "ends_on", "is_enabled", "description",
)


def _get_account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise NotFoundError("account not found")
    return account


def _get_rule(db: Session, account_id: int, rule_id: int) -> RewardRule:
    rule = db.get(RewardRule, rule_id)
    if rule is None or rule.account_id != account_id:
        raise NotFoundError("rule not found")
    return rule


def _check_payload(payload: RewardRuleIn, current: RewardRule | None) -> None:
    method = payload.method
    if method == "percent":
        if payload.rate is None:
            raise ValidationError("rate", "required for percent rules")
        if payload.fixed_amount is not None:
            raise ValidationError("fixed_amount", "only fixed rules have an amount")
        if payload.unit_spend is not None:
            raise ValidationError("unit_spend", "only per_unit rules have a unit spend")
    elif method == "fixed":
        if payload.fixed_amount is None:
            raise ValidationError("fixed_amount", "required for fixed rules")
        if payload.rate is not None:
            raise ValidationError("rate", "only percent rules have a rate")
        if payload.unit_spend is not None:
            raise ValidationError("unit_spend", "only per_unit rules have a unit spend")
    else:
        if payload.unit_spend is None:
            raise ValidationError("unit_spend", "required for per_unit rules")
        if payload.rate is not None:
            raise ValidationError("rate", "only percent rules have a rate")
        if payload.fixed_amount is not None:
            raise ValidationError("fixed_amount", "only fixed rules have an amount")
    if payload.fixed_amount is not None and payload.fixed_amount <= 0:
        # #289: a stored MOZE placeholder (fixed, 0) may be re-saved as such; nothing else may be 0.
        keeps_placeholder = (
            current is not None and current.method == "fixed" and current.fixed_amount == 0 and method == "fixed"
        )
        if not keeps_placeholder:
            raise ValidationError("fixed_amount", "must be greater than 0")
    if payload.starts_on and payload.ends_on and payload.starts_on > payload.ends_on:
        raise ValidationError("ends_on", "must not be before starts_on")


def _check_refs(db: Session, payload: RewardRuleIn, account_id: int, current: RewardRule | None) -> None:
    """A *new* reference must be live; an unchanged one (whole-row PUT) or the card itself may be archived.

    The SPA's 啟用 switch and 停用 resend the whole row, so a legacy rule pointing at a since-archived points
    account or project must still save; the card itself is always accepted (spec §5.1: null = the card itself,
    and the MOZE import stored the card id explicitly).
    """
    for field, model in (("reward_account_id", Account), ("reward_project_id", Project)):
        ref_id = getattr(payload, field)
        if ref_id is None:
            continue
        ref = db.get(model, ref_id)
        if ref is None:
            raise ValidationError(field, "must be an existing, non-archived account" if model is Account
                                  else "must be an existing, non-archived project")
        unchanged = current is not None and getattr(current, field) == ref_id
        is_card = model is Account and ref_id == account_id
        if ref.is_archived and not (unchanged or is_card):
            raise ValidationError(field, "must be an existing, non-archived account" if model is Account
                                  else "must be an existing, non-archived project")


def _write_fields(rule: RewardRule, payload: RewardRuleIn) -> None:
    for field in _WRITE_FIELDS:
        setattr(rule, field, getattr(payload, field))


def _check_basic_overlap(db: Session, account_id: int, rule: RewardRule) -> None:
    """Two enabled basic rules of one account must not share a day; a null bound is open-ended."""
    if not (rule.is_enabled and rule.is_basic):
        return
    query = select(RewardRule).where(
        RewardRule.account_id == account_id, RewardRule.is_enabled.is_(True), RewardRule.is_basic.is_(True)
    ).order_by(RewardRule.id)
    if rule.id is not None:
        query = query.where(RewardRule.id != rule.id)
    for other in db.execute(query).scalars():
        if _intersects(rule.starts_on, rule.ends_on, other.starts_on, other.ends_on):
            raise CodedConflictError(BASIC_RULE_OVERLAP, f"rule {other.id}")


def _intersects(a_start: date | None, a_end: date | None, b_start: date | None, b_end: date | None) -> bool:
    return (a_start is None or b_end is None or a_start <= b_end) and (b_start is None or a_end is None or b_start <= a_end)


def _apply_share_cap(db: Session, rule: RewardRule, share_cap_with: ShareCapWith) -> None:
    if share_cap_with is None:
        return
    if share_cap_with == "none":
        rule.shared_cap_id = None
        return
    other = db.get(RewardRule, share_cap_with)
    if other is None or other.account_id != rule.account_id or other is rule:
        raise ValidationError("share_cap_with", "must be another rule of the same account")
    if other.shared_cap_id is None:
        other.shared_cap_id = uuid4()
    rule.shared_cap_id = other.shared_cap_id


def _group_rules(db: Session, rule: RewardRule) -> list[RewardRule]:
    if rule.shared_cap_id is None:
        return [rule]
    return list(db.scalars(
        select(RewardRule).where(RewardRule.shared_cap_id == rule.shared_cap_id).order_by(RewardRule.id)))


def group_rule_ids(db: Session, rule: RewardRule) -> list[int]:
    """The ids of the rules sharing this rule's cap, or just this rule when ungrouped."""
    return [r.id for r in _group_rules(db, rule)]


def group_cap(db: Session, rule: RewardRule) -> Decimal | None:
    """The smallest non-null total_cap in the rule's group; None when every rule is uncapped."""
    caps = [r.total_cap for r in _group_rules(db, rule) if r.total_cap is not None]
    return min(caps) if caps else None


def _check_group_window(db: Session, rule: RewardRule, payload: RewardRuleIn) -> None:
    if rule.shared_cap_id is None:
        return
    if len({r.window for r in _group_rules(db, rule)}) > 1:
        field = "share_cap_with" if isinstance(payload.share_cap_with, int) else "window"
        raise ValidationError(field, "rules sharing a cap must use the same window")


def _save(db: Session, rule: RewardRule, payload: RewardRuleIn, *, is_new: bool) -> None:
    """Shared tail of create and update: refs → write fields → overlap → share cap → flush."""
    _check_refs(db, payload, rule.account_id, None if is_new else rule)
    _write_fields(rule, payload)
    db.flush()  # a new rule needs its id so the overlap query can exclude it
    _check_basic_overlap(db, rule.account_id, rule)
    _apply_share_cap(db, rule, payload.share_cap_with)
    db.flush()
    _check_group_window(db, rule, payload)


def create(db: Session, account_id: int, payload: RewardRuleIn) -> int:
    _get_account(db, account_id)
    _check_payload(payload, None)
    sort_order = payload.sort_order
    if sort_order is None:
        top = db.scalar(select(func.max(RewardRule.sort_order)).where(RewardRule.account_id == account_id))
        sort_order = 0 if top is None else top + 1
    rule = RewardRule(account_id=account_id, sort_order=sort_order)
    db.add(rule)
    _save(db, rule, payload, is_new=True)
    if rule.shared_cap_id is not None:
        # Joining a shared cap can lower the group cap: recompute the group's estimates as a PUT does (spec 5.1).
        from . import reward_engine  # reward_engine imports this module

        reward_engine.mark_early_rechecks(db, account_id)
        reward_engine.recompute_rules(db, group_rule_ids(db, rule))
    return rule.id


def update(db: Session, account_id: int, rule_id: int, payload: RewardRuleIn) -> None:
    """Rewrite the rule, then re-bucket and recompute the estimated accruals of every rule whose window or cap it
    may have moved: the rule and its shared-cap group before and after (reward engine spec 5.1). The card's open
    `reward_early` cases get a recheck first (P6; statement rows before accrual locks, P12)."""
    from . import reward_engine  # reward_engine imports this module

    rule = _get_rule(db, account_id, rule_id)
    touched = set(group_rule_ids(db, rule))
    _check_payload(payload, rule)
    if payload.sort_order is not None:
        rule.sort_order = payload.sort_order
    _save(db, rule, payload, is_new=False)
    touched |= set(group_rule_ids(db, rule))
    reward_engine.mark_early_rechecks(db, account_id)
    reward_engine.recompute_rules(db, touched)


def reorder(db: Session, account_id: int, ids: list[int]) -> None:
    """Write positions 0..n-1; the list must name every rule of the account exactly once."""
    _get_account(db, account_id)
    rules = {r.id: r for r in db.execute(select(RewardRule).where(RewardRule.account_id == account_id)).scalars()}
    if len(ids) != len(set(ids)) or set(ids) != set(rules):
        raise ValidationError("ids", "must list every rule of the account exactly once")
    for position, rule_id in enumerate(ids):
        rules[rule_id].sort_order = position
    db.flush()


def delete(db: Session, account_id: int, rule_id: int) -> None:
    rule = _get_rule(db, account_id, rule_id)
    entries = db.scalar(select(func.count()).select_from(EntryRewardRule).where(EntryRewardRule.rule_id == rule.id))
    rewards = db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.reward_rule_id == rule.id))
    if entries or rewards:
        raise CodedConflictError(RULE_REFERENCED, json.dumps({"entries": entries, "rewards": rewards}))
    db.delete(rule)
    db.flush()


def rule_state(rule: RewardRule, today: date) -> str:
    if not rule.is_enabled:
        return "disabled"
    if rule.ends_on is not None and rule.ends_on < today:
        return "expired"
    if rule.starts_on is not None and rule.starts_on > today:
        return "upcoming"
    return "active"


def _one_year_ago(today: date) -> date:
    try:
        return date(today.year - 1, today.month, today.day)
    except ValueError:  # Feb 29 -> Feb 28
        return date(today.year - 1, today.month, 28)


def _rule_stats(db: Session, rule_ids: list[int], today: date) -> dict[int, dict]:
    stats = {
        rid: {"linked_entries": 0, "linked_entries_12m": 0, "rewards_count": 0, "rewards_total": [],
              "rewards_last_date": None}
        for rid in rule_ids
    }
    if not rule_ids:
        return stats
    since = _one_year_ago(today)
    linked = db.execute(
        select(
            EntryRewardRule.rule_id,
            func.count(),
            func.count().filter(LedgerEntry.entry_date.between(since, today)),
        )
        .join(LedgerEntry, LedgerEntry.id == EntryRewardRule.entry_id)
        .where(EntryRewardRule.rule_id.in_(rule_ids))
        .group_by(EntryRewardRule.rule_id)
    ).all()
    for rule_id, total, recent in linked:
        stats[rule_id]["linked_entries"] = total
        stats[rule_id]["linked_entries_12m"] = recent
    rewards = db.execute(
        select(LedgerEntry.reward_rule_id, LedgerEntry.currency, func.count(), func.sum(LedgerEntry.amount))
        .where(LedgerEntry.kind == "reward", LedgerEntry.reward_rule_id.in_(rule_ids))
        .group_by(LedgerEntry.reward_rule_id, LedgerEntry.currency)
        .order_by(LedgerEntry.currency)
    ).all()
    for rule_id, currency, count, amount in rewards:
        stats[rule_id]["rewards_count"] += count
        stats[rule_id]["rewards_total"].append(
            {"currency": currency, "amount": Decimal(amount).quantize(Decimal("0.0001"))})
    last = db.execute(
        select(LedgerEntry.reward_rule_id, func.max(LedgerEntry.entry_date))
        .where(LedgerEntry.kind == "reward", LedgerEntry.reward_rule_id.in_(rule_ids))
        .group_by(LedgerEntry.reward_rule_id)
    ).all()
    for rule_id, last_date in last:
        stats[rule_id]["rewards_last_date"] = last_date
    return stats


def overview(db: Session, *, account_ids: list[int] | None, state: str, include_archived_accounts: bool) -> dict:
    today = ledger_service._today()
    query = select(Account).order_by(Account.sort_order, Account.name)
    if account_ids:
        query = query.where(Account.id.in_(account_ids))
    if not include_archived_accounts:
        keep = Account.is_archived.is_(False)
        query = query.where(keep | Account.id.in_(account_ids)) if account_ids else query.where(keep)
    accounts = db.scalars(query).all()
    rules = db.scalars(
        select(RewardRule)
        .where(RewardRule.account_id.in_([a.id for a in accounts]))
        .order_by(RewardRule.sort_order, RewardRule.id)
    ).all() if accounts else []
    states = {rule.id: rule_state(rule, today) for rule in rules}
    counts = {key: 0 for key in ("active", "disabled", "expired", "upcoming")}
    for value in states.values():
        counts[value] += 1
    counts["enabled_expired"] = sum(1 for r in rules if r.is_enabled and r.ends_on is not None and r.ends_on < today)
    shown = [r for r in rules if state == "all" or states[r.id] == state]
    stats = _rule_stats(db, [r.id for r in shown], today)
    by_account: dict[int, list[dict]] = {a.id: [] for a in accounts}
    for rule in shown:
        by_account[rule.account_id].append({**ledger_service.rule_out(rule), "state": states[rule.id],
                                            "stats": stats[rule.id]})
    return {
        "accounts": [
            {"account_id": a.id, "account_name": a.name, "is_credit": a.is_credit, "is_archived": a.is_archived,
             "rules": by_account[a.id]}
            for a in accounts
        ],
        "counts": counts,
    }
