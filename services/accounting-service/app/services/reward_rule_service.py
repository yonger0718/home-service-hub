"""Reward rule writes: create and update under /accounts/{id}/reward-rules (reward rules design §5)."""
import json
from datetime import date
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Account, EntryRewardRule, LedgerEntry, Project, RewardRule
from ..schemas.writes import RewardRuleIn, ShareCapWith
from .errors import CodedConflictError, NotFoundError, ValidationError

BASIC_RULE_OVERLAP = "basic_rule_overlap"
RULE_REFERENCED = "rule_referenced"

# Columns copied from the payload verbatim; sort_order and share_cap_with have their own handling.
_WRITE_FIELDS = (
    "name", "method", "rate", "fixed_amount", "window", "posting", "delay_days", "post_month_offset", "post_day",
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


def _check_payload(payload: RewardRuleIn) -> None:
    if payload.method == "percent":
        if payload.rate is None:
            raise ValidationError("rate", "required for percent rules")
        if payload.fixed_amount is not None:
            raise ValidationError("fixed_amount", "only fixed rules have an amount")
    else:
        if payload.fixed_amount is None:
            raise ValidationError("fixed_amount", "required for fixed rules")
        if payload.rate is not None:
            raise ValidationError("rate", "only percent rules have a rate")
    if payload.starts_on and payload.ends_on and payload.starts_on > payload.ends_on:
        raise ValidationError("ends_on", "must not be before starts_on")


def _check_refs(db: Session, payload: RewardRuleIn) -> None:
    for field, model in (("reward_account_id", Account), ("reward_project_id", Project)):
        ref_id = getattr(payload, field)
        if ref_id is None:
            continue
        ref = db.get(model, ref_id)
        if ref is None or ref.is_archived:
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


def _save(db: Session, rule: RewardRule, payload: RewardRuleIn) -> None:
    """Shared tail of create and update: refs → write fields → overlap → share cap → flush."""
    _check_refs(db, payload)
    _write_fields(rule, payload)
    db.flush()  # a new rule needs its id so the overlap query can exclude it
    _check_basic_overlap(db, rule.account_id, rule)
    _apply_share_cap(db, rule, payload.share_cap_with)
    db.flush()


def create(db: Session, account_id: int, payload: RewardRuleIn) -> int:
    _get_account(db, account_id)
    _check_payload(payload)
    sort_order = payload.sort_order
    if sort_order is None:
        top = db.scalar(select(func.max(RewardRule.sort_order)).where(RewardRule.account_id == account_id))
        sort_order = 0 if top is None else top + 1
    rule = RewardRule(account_id=account_id, sort_order=sort_order)
    db.add(rule)
    _save(db, rule, payload)
    return rule.id


def update(db: Session, account_id: int, rule_id: int, payload: RewardRuleIn) -> None:
    rule = _get_rule(db, account_id, rule_id)
    _check_payload(payload)
    if payload.sort_order is not None:
        rule.sort_order = payload.sort_order
    _save(db, rule, payload)


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
