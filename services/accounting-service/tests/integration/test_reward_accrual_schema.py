from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import RewardAccrual, RewardRule
from tests.helpers import make_account, make_entry


def _rule(session, card, **columns):
    values = dict(account_id=card.id, name="測試規則", method="percent", rate=Decimal("1"), posting="after_window")
    values.update(columns)
    rule = RewardRule(**values)
    session.add(rule)
    session.flush()
    return rule


def _seed(session):
    card = make_account(session, "測試卡")
    return card, _rule(session, card), make_entry(session, card, "100")


def _accrual(entry, rule, **columns):
    values = dict(source_entry_id=entry.id if entry else None, rule_id=rule.id, window_start=date(2026, 10, 1),
                  window_end=date(2026, 10, 31), estimate=Decimal("10"), amount=Decimal("10"))
    values.update(columns)
    return RewardAccrual(**values)


def test_accrual_round_trip(db_session):
    _, rule, entry = _seed(db_session)
    db_session.add(_accrual(entry, rule))
    db_session.commit()
    row = db_session.query(RewardAccrual).one()
    assert row.state == "estimated"
    assert row.updated_at is not None
    assert row.post_on is None and row.ledger_entry_id is None


def test_accrual_unique_per_source_rule(db_session):
    _, rule, entry = _seed(db_session)
    db_session.add(_accrual(entry, rule))
    db_session.commit()
    db_session.add(_accrual(entry, rule))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
    db_session.add_all([_accrual(None, rule), _accrual(None, rule)])
    db_session.commit()
    assert db_session.query(RewardAccrual).count() == 3


def test_source_delete_sets_null(db_session):
    _, rule, entry = _seed(db_session)
    db_session.add(_accrual(entry, rule))
    db_session.commit()
    db_session.execute(text("DELETE FROM ledger_entry WHERE id = :i"), {"i": entry.id})
    db_session.commit()
    db_session.expire_all()
    row = db_session.query(RewardAccrual).one()
    assert row.source_entry_id is None


def test_ledger_reward_pair_unique(db_session):
    card, rule, entry = _seed(db_session)
    make_entry(db_session, card, "1", kind="reward", reward_source_entry_id=entry.id, reward_rule_id=rule.id)
    db_session.commit()
    with pytest.raises(IntegrityError):
        make_entry(db_session, card, "1", kind="reward", reward_source_entry_id=entry.id, reward_rule_id=rule.id)
    db_session.rollback()
    make_entry(db_session, card, "1", kind="reward", reward_source_entry_id=entry.id, reward_rule_id=None)
    make_entry(db_session, card, "1", kind="reward", reward_source_entry_id=entry.id, reward_rule_id=None)
    # the partial index ignores a row with no source as well (e.g. a hand-booked reward naming only its rule)
    make_entry(db_session, card, "1", kind="reward", reward_source_entry_id=None, reward_rule_id=rule.id)
    make_entry(db_session, card, "1", kind="reward", reward_source_entry_id=None, reward_rule_id=rule.id)
    db_session.commit()


def test_new_enum_values_accepted(db_session):
    card = make_account(db_session, "測試卡")
    rule = _rule(db_session, card, window="calendar_month", method="per_unit", unit_spend=Decimal("20"))
    db_session.commit()
    assert rule.window == "calendar_month" and rule.unit_spend == Decimal("20")
