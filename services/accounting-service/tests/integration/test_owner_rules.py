"""Owner-taught reconciliation rules (inbox spec §6). Synthetic data only."""
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import ReconciliationOwnerRule
from app.services.reconciliation_actions import audit


def _rule(seed, db, account, **fields) -> ReconciliationOwnerRule:
    defaults = dict(line_kind="purchase", merchant_pattern="放款還款", flow_sign="-", amount=Decimal("3865"),
                    action="record", params={"category_id": 1})
    row = ReconciliationOwnerRule(account_id=account.id, **{**defaults, **fields})
    db.add(row)
    db.commit()
    return row


def test_rule_row_defaults(seed, db_session):
    account = seed.account("樂天")
    row = _rule(seed, db_session, account)
    db_session.expire_all()
    row = db_session.get(ReconciliationOwnerRule, row.id)
    assert row.enabled is True and row.applied_count == 0 and row.last_applied_at is None
    assert row.amount == Decimal("3865.0000") and row.params == {"category_id": 1}


def test_rule_key_is_unique_per_account(seed, db_session):
    account = seed.account("樂天")
    _rule(seed, db_session, account)
    with pytest.raises(IntegrityError):
        _rule(seed, db_session, account)
    db_session.rollback()


def test_rule_key_treats_null_amounts_as_equal(seed, db_session):
    account = seed.account("樂天")
    _rule(seed, db_session, account, amount=None)
    with pytest.raises(IntegrityError):
        _rule(seed, db_session, account, amount=None)
    db_session.rollback()


def test_new_audit_events_are_known():
    assert {"rule_applied", "rule_skipped", "inbox_answer"} <= audit.EVENTS
