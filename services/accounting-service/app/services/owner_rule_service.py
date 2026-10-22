"""Owner-taught reconciliation rules (inbox spec §6): the rule key of an inbox answer, learning it, and managing rows.

`learn` runs from `inbox_service.apply_batch` after every member of the tap applied (§6.2); it flushes, the caller
commits. `apply_rules` / `run_rules_safe` apply the rules to a live statement's open bank-line cases right before
the policy (§6.3); like `policy_service.run_policy` the runner commits per case and is only called by routers after
their own commit.
"""
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.models import (
    AccountStatement, ReconciliationAction, ReconciliationAudit, ReconciliationCase, ReconciliationOwnerRule,
    StatementLine,
)
from app.services import inbox_service, policy_service, proposal_service, reconciliation_actions
from app.services import reconciliation_service as rec
from app.services.errors import CodedValidationError, NotFoundError, ValidationError
from app.services.inbox_service import flow_sign, learnable, pattern
from app.services.reconciliation_actions import audit, engine
from app.services.reconciliation_actions.base import Context, current_line

log = logging.getLogger(__name__)

RECORD_PARAMS = ("category_id", "name", "project_id")
QUESTION_ACTIONS = {"bank_only": ("record", "dismiss"), "amount_differs": ("accept_bank_amount",)}


@dataclass(frozen=True)
class RuleKey:
    """The unique key of a rule (`uq_reconciliation_owner_rule_key`); `amount` None = any amount."""
    account_id: int
    line_kind: str
    merchant_pattern: str
    flow_sign: str
    amount: Decimal | None


def rule_key_for(db: Session, cases: list[ReconciliationCase], lines: list[StatementLine | None], *,
                 amount_exact: bool, button: str = "record") -> RuleKey:
    """The one key every member's bank line gives (spec §6.1/§6.2): the account of each case's statement, the line's
    kind, digit-stripped merchant and flow sign, and — when `amount_exact` and the answer is not
    `accept_bank_amount` — its flow amount. 422 `rule_pattern_mismatch` when the members differ, `rule_pattern_empty`
    when the merchant has no letters left, `not_learnable` for a member without a line."""
    shapes, amounts = set(), set()
    for case, line in zip(cases, lines, strict=True):
        if line is None:
            raise CodedValidationError("learn", "not_learnable", f"case {case.id} has no statement line")
        statement = db.get(AccountStatement, case.statement_id)
        shapes.add((statement.account_id, str(line.line_kind), pattern(line.merchant_norm or ""),
                    flow_sign(line.flow_amount)))
        amounts.add(line.flow_amount)
    exact = amount_exact and button != "accept_bank_amount"
    if len(shapes) != 1 or (exact and len(amounts) != 1):
        raise CodedValidationError("learn", "rule_pattern_mismatch")
    ((account_id, line_kind, merchant_pattern, sign),) = shapes
    if not merchant_pattern:
        raise CodedValidationError("learn", "rule_pattern_empty")
    return RuleKey(account_id=account_id, line_kind=line_kind, merchant_pattern=merchant_pattern, flow_sign=sign,
                   amount=next(iter(amounts)) if exact else None)


def learn(db: Session, *, question: str, button: str, key: RuleKey, params: dict,
          created_from_case_id: int) -> ReconciliationOwnerRule:
    """Create the rule for `key`, or — same key taught again — replace its answer and re-enable it (one upsert; the
    NULLS NOT DISTINCT unique index makes a NULL amount conflict too). Flushes; the caller commits."""
    if not learnable(question, button):
        raise CodedValidationError("learn", "not_learnable", f"{button} on {question}")
    stored = ({name: params[name] for name in RECORD_PARAMS if params.get(name) is not None}
              if button == "record" else {})
    table = ReconciliationOwnerRule.__table__
    stmt = (postgresql.insert(ReconciliationOwnerRule)
            .values(account_id=key.account_id, line_kind=key.line_kind, merchant_pattern=key.merchant_pattern,
                    flow_sign=key.flow_sign, amount=key.amount, action=button, params=stored,
                    created_from_case_id=created_from_case_id))
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.account_id, table.c.line_kind, table.c.merchant_pattern, table.c.flow_sign,
                        table.c.amount],
        set_={"action": button, "params": stored, "enabled": True, "created_from_case_id": created_from_case_id},
    ).returning(ReconciliationOwnerRule.id)
    rule_id = db.execute(stmt).scalar_one()
    db.flush()
    return db.get(ReconciliationOwnerRule, rule_id, populate_existing=True)


def list_rules(db: Session) -> list[ReconciliationOwnerRule]:
    return list(db.execute(select(ReconciliationOwnerRule)
                           .order_by(ReconciliationOwnerRule.account_id, ReconciliationOwnerRule.id)).scalars())


def _get(db: Session, rule_id: int) -> ReconciliationOwnerRule:
    row = db.get(ReconciliationOwnerRule, rule_id)
    if row is None:
        raise NotFoundError(f"owner rule {rule_id} not found")
    return row


def delete_rule(db: Session, rule_id: int) -> None:
    db.delete(_get(db, rule_id))
    db.flush()


def set_enabled(db: Session, rule_id: int, enabled: bool) -> ReconciliationOwnerRule:
    row = _get(db, rule_id)
    row.enabled = enabled
    db.flush()
    return row


# --- applying rules (spec §6.3) ---------------------------------------------------------------------------------

@dataclass
class RuleRunResult:
    applied: list[int] = field(default_factory=list)  # action ids
    skipped: list[tuple[int, str]] = field(default_factory=list)  # (case_id, code)


def reverted_rule_ids(db: Session, case: ReconciliationCase) -> set[int]:
    """Every rule whose action 撤銷 undid on this case (spec §8: the same rule never re-answers a case it was reverted
    on): the `rule:<id>` actor of the `action_applied` audit of each `reverted` action of the case. A malformed actor
    is ignored instead of aborting the statement's run."""
    actors = db.execute(select(ReconciliationAudit.actor).join(
        ReconciliationAction, ReconciliationAction.id == ReconciliationAudit.action_id).where(
        ReconciliationAction.case_id == case.id, ReconciliationAction.status == "reverted",
        ReconciliationAudit.event == "action_applied")).scalars()
    ids = set()
    for actor in actors:
        if not actor or not actor.startswith("rule:"):
            continue
        try:
            ids.add(int(actor.removeprefix("rule:")))
        except ValueError:
            continue
    return ids


def match_rule(rules: Iterable[ReconciliationOwnerRule], *, line_kind: str, merchant_norm: str,
               flow_amount: Decimal) -> ReconciliationOwnerRule | None:
    """The enabled rule answering a line: same `line_kind` and flow sign, `merchant_pattern` (non-empty) a substring
    of the line's digit-stripped merchant, `amount` None or equal to the flow amount. Longest pattern wins, then
    lowest id."""
    text, sign = pattern(merchant_norm or ""), flow_sign(flow_amount)
    hits = [r for r in rules
            if r.enabled and r.line_kind == line_kind and r.flow_sign == sign and r.merchant_pattern
            and r.merchant_pattern in text and (r.amount is None or Decimal(r.amount) == flow_amount)]
    return min(hits, key=lambda r: (-len(r.merchant_pattern), r.id), default=None)


def _eligible(db: Session, case: ReconciliationCase, question: str,
              rules: list[ReconciliationOwnerRule]) -> list[ReconciliationOwnerRule]:
    """The rules whose answer the case's question can take (`amount_differs` only with a standalone, unprotected
    candidate; the cutover lock is left to the engine), minus every rule whose action 撤銷 undid on this case."""
    actions = QUESTION_ACTIONS.get(question, ())
    if question == "amount_differs" and not inbox_service.adjustable(db, case, check_lock=False):
        return []
    reverted = reverted_rule_ids(db, case)
    return [r for r in rules if r.action in actions and r.id not in reverted]


def _precheck(version: int, action: str):
    """Under the statement and case locks: the case is still at the version the rule judged, and — for a creation
    (`create_entry`) — no owner entry may already record the line (`policy_service.refuse_owner_recorded`, the guard
    the policy's creations use, looked up per call). `adjust_amount` / `dismiss` book nothing; for an `amount_delta`
    the near owner entry is the candidate itself, which the guard would always see as a twin."""
    stale = inbox_service.expect_version(version)

    def check(ctx: Context) -> None:
        stale(ctx)
        if action == "create_entry":
            policy_service.refuse_owner_recorded(ctx)
    return check


def _skip(db: Session, statement_id: int, case_id: int, rule_id: int | None, code: str, result: RuleRunResult, *,
          run_id: int | None) -> None:
    """Audit `rule_skipped` in its own transaction, once per (case, version, rule, code) — a deterministic refusal
    repeats on every run (`policy_service._skip`'s dedup). Read-version -> check -> insert under the statement ->
    case locks (the engine's order: the audit row's FK takes FOR KEY SHARE on the statement and the case)."""
    result.skipped.append((case_id, code))
    try:
        rec._lock_statement(db, statement_id)
    except NotFoundError:
        db.rollback()
        return
    version = db.execute(select(ReconciliationCase.version).where(ReconciliationCase.id == case_id)
                         .with_for_update()).scalar_one_or_none()
    stored_version = ReconciliationAudit.detail["case_version"].astext
    stored_rule = ReconciliationAudit.detail["rule_id"].astext
    already = db.execute(select(ReconciliationAudit.id).where(
        ReconciliationAudit.event == "rule_skipped", ReconciliationAudit.case_id == case_id,
        ReconciliationAudit.detail["code"].astext == code,
        stored_rule.is_(None) if rule_id is None else stored_rule == str(rule_id),
        stored_version.is_(None) if version is None else stored_version == str(version)).limit(1)).first()
    if already is None:
        audit.record(db, event="rule_skipped", actor="owner" if rule_id is None else f"rule:{rule_id}",
                     statement_id=statement_id, case_id=case_id, run_id=run_id,
                     detail={"rule_id": rule_id, "case_id": case_id, "code": code, "case_version": version})
    db.commit()


def _apply_case(db: Session, statement_id: int, case: ReconciliationCase, line: StatementLine,
                rule: ReconciliationOwnerRule, result: RuleRunResult, *, run_id: int | None) -> None:
    """One case in its own transaction: the rule's answer through the engine as the owner, committed, or refused,
    rolled back and audited."""
    rule_id, case_id = rule.id, case.id
    label = f"rule:{rule_id}"
    try:
        try:
            action, params = inbox_service.translate(rule.action, rule.params or {}, case, line, answer=label)
        except ValidationError:  # no line / candidate for the answer now
            db.rollback()
            _skip(db, statement_id, case_id, rule_id, "not_offered", result, run_id=run_id)
            return
        row, replayed = engine.apply_action(db, case_id=case_id, action=action, params=params, actor="owner",
                                            label=label, run_id=run_id, precheck=_precheck(case.version, action))
    except inbox_service.REFUSALS as exc:
        db.rollback()
        _skip(db, statement_id, case_id, rule_id, inbox_service._refusal_code(exc), result, run_id=run_id)
        return
    if replayed:  # an identical answer already stands: nothing new to count
        db.rollback()
        _skip(db, statement_id, case_id, rule_id, "replayed", result, run_id=run_id)
        return
    rule.applied_count = ReconciliationOwnerRule.applied_count + 1
    rule.last_applied_at = func.now()
    audit.record(db, event="rule_applied", actor=label, statement_id=statement_id, case_id=case_id,
                 action_id=row.id, run_id=run_id, detail={"rule_id": rule_id, "case_id": case_id, "action": action})
    db.commit()
    result.applied.append(row.id)


def apply_rules(db: Session, statement_id: int, *, run_id: int | None = None) -> RuleRunResult:
    """Apply the account's enabled rules to one statement that the policy's exclusions (bar the currency gate,
    `policy_service.statement_excluded`) leave open to automatic answers (spec §6.3): each `open` case with a
    current line, in id order, whose question is `bank_only` (→ `record` / `dismiss` rules) or `amount_differs` with
    an adjustable candidate (→ `accept_bank_amount` rules), never a settlement line or a case with a pending
    proposal; the matching rule (`match_rule`, the rule 撤銷 undid on the case excluded) is applied through the
    engine as the owner. Commits per case; a refusal is audited `rule_skipped` and not retried in this run."""
    result = RuleRunResult()
    statement = db.get(AccountStatement, statement_id, populate_existing=True)
    if statement is None or policy_service.statement_excluded(db, statement) is not None:
        return result  # not live, conflict_open, needs_recheck, open parse_review / statement_conflict
    account_id = statement.account_id
    rules = list(db.execute(select(ReconciliationOwnerRule).where(
        ReconciliationOwnerRule.account_id == account_id, ReconciliationOwnerRule.enabled.is_(True))
        .order_by(ReconciliationOwnerRule.id)).scalars())
    if not rules:
        return result
    case_ids = list(db.execute(select(ReconciliationCase.id).where(
        ReconciliationCase.statement_id == statement_id, ReconciliationCase.status == "open")
        .order_by(ReconciliationCase.id)).scalars())
    for case_id in case_ids:
        case = db.get(ReconciliationCase, case_id, populate_existing=True)
        statement = db.get(AccountStatement, statement_id)
        if case is None or case.status != "open" or statement is None:
            continue
        line = current_line(db, case, statement)
        if line is None or proposal_service.pending_for_case(db, case.id):
            continue
        question = inbox_service._question(case, statement, line, reconciliation_actions.allowed(case))
        if question not in QUESTION_ACTIONS:  # settlement, transfer, other, proposed, ...: never a rule's
            continue
        rule = match_rule(_eligible(db, case, question, rules), line_kind=str(line.line_kind),
                          merchant_norm=line.merchant_norm or "", flow_amount=line.flow_amount)
        if rule is not None:
            _apply_case(db, statement_id, case, line, rule, result, run_id=run_id)
    return result


def run_rules_safe(db: Session, statement_id: int, *, run_id: int | None = None) -> RuleRunResult:
    """For the routes, after their commit and right before `policy_service.run_policy_safe`: any error rolls back
    and is only audited (`rule_skipped` with the exception class, `rule_id` None), never raised."""
    try:
        return apply_rules(db, statement_id, run_id=run_id)
    except Exception as exc:  # noqa: BLE001  (a rule never fails the caller)
        log.exception("owner-rule run failed for statement %s", statement_id)
        db.rollback()
        try:
            audit.record(db, event="rule_skipped", actor="owner", statement_id=statement_id, run_id=run_id,
                         detail={"rule_id": None, "code": exc.__class__.__name__})
            db.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not audit the failed owner-rule run for statement %s", statement_id)
            db.rollback()
        return RuleRunResult()
