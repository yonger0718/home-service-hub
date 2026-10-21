"""Owner-taught reconciliation rules (inbox spec §6): the rule key of an inbox answer, learning it, and managing rows.

`learn` runs from `inbox_service.apply_batch` after every member of the tap applied (§6.2); it flushes, the caller
commits. Task 6 adds the runner that applies the rules to new statements (§6.3).
"""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.models import AccountStatement, ReconciliationCase, ReconciliationOwnerRule, StatementLine
from app.services.errors import CodedValidationError, NotFoundError
from app.services.inbox_service import flow_sign, learnable, pattern

RECORD_PARAMS = ("category_id", "name", "project_id")


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
