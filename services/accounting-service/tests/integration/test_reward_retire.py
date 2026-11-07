"""Retiring a statement releases its bank-wins outcomes (#297 M9, owner ruling 2026-11-07): the accruals it
confirmed or forfeited go back to `posted` at their engine amount, unconfirmed, so another statement can pair them;
its applied `accept_early_reward` actions no longer block the retire and are retired with it. Synthetic data only."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationAudit
from app.services import statement_retire_service as retire_svc
from app.services.errors import CodedConflictError
from sqlalchemy.orm import sessionmaker
from tests.integration.statement_fixtures import _card, _line, _reconcile, _submit, run  # noqa: F401
from tests.integration.test_reward_bank_wins import (
    PERIOD, SHOP, _accrual_of, _carried_and_credited_current, _post, _reward_line, _rule, _spend, _test_card,
)
from tests.integration.test_reward_bank_wins_cases import _accept, _early_statement, _one_open

BUY_DAY = date(2026, 10, 20)  # posts 10-25


def _retire(db, statement):
    result = retire_svc.retire_statement(db, account_id=statement.account_id, statement_id=statement.id,
                                         note="重複", label="owner-test", request_id=None)
    db.commit()
    return result


def _state(db, purchase):
    accrual = _accrual_of(db, purchase)
    return accrual.state, accrual.confirmed_statement_id, db.get(LedgerEntry, accrual.ledger_entry_id).amount


def _confirmed(seed, db, run):
    card = _test_card(seed, db)
    rule = _rule(db, card)
    buy = _spend(db, card, 1000, BUY_DAY, rule)
    db.commit()
    _post(db, buy)
    stmt, _, _ = _submit(db, run, card, [_line(1, BUY_DAY, SHOP, 1000), _reward_line(2, 9)], period=PERIOD)
    assert _state(db, buy) == ("bank_confirmed", stmt.id, Decimal("9.0000"))
    return card, rule, buy, stmt


def test_retire_returns_a_confirmed_accrual_to_posted_and_the_twin_pairs_it(seed, db_session, run):
    """The production shape: a duplicate of the same month with another period end. The kept twin could not take
    the accrual while the duplicate held it; after the retire it is flagged and its reconcile confirms it."""
    card = _card(seed, db_session, "測試卡", closing_day=None)  # calendar month: both periods pass the guardrails
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, BUY_DAY, rule)
    db_session.commit()
    _post(db_session, buy)
    lines = lambda: [_line(1, BUY_DAY, SHOP, 1000), _reward_line(2, 9, date(2026, 10, 28))]  # noqa: E731
    stmt, _, _ = _submit(db_session, run, card, lines(), period=(date(2026, 10, 1), date(2026, 10, 31)))
    twin, _, _ = _submit(db_session, run, card, lines(), period=(date(2026, 10, 1), date(2026, 10, 30)))
    assert twin.id != stmt.id
    assert _state(db_session, buy) == ("bank_confirmed", stmt.id, Decimal("9.0000"))

    _retire(db_session, stmt)

    assert _state(db_session, buy) == ("posted", None, Decimal("10.0000"))
    assert _accrual_of(db_session, buy).amount == Decimal("10.0000")
    assert db_session.get(AccountStatement, twin.id).needs_recheck is True
    _reconcile(db_session, twin.id)
    assert _state(db_session, buy) == ("bank_confirmed", twin.id, Decimal("9.0000"))


def test_retire_returns_a_forfeited_accrual_to_posted(seed, db_session, run, monkeypatch):
    t = _carried_and_credited_current(seed, db_session, run, monkeypatch)
    assert _state(db_session, t.carried) == ("forfeited", t.stmt.id, Decimal("0.0000"))

    _retire(db_session, t.stmt)

    assert _state(db_session, t.carried) == ("posted", None, Decimal("10.0000"))
    assert _state(db_session, t.current) == ("posted", None, Decimal("20.0000"))


def test_retire_leaves_accruals_of_other_statements_alone(seed, db_session, run):
    card, rule, buy, stmt = _confirmed(seed, db_session, run)
    other = _spend(db_session, card, 500, date(2026, 11, 20), rule)
    db_session.commit()
    _post(db_session, other)
    later, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 11, 20), SHOP, 500),
                                                 _reward_line(2, 4, date(2026, 12, 10))],
                          period=(date(2026, 11, 16), date(2026, 12, 15)))
    assert _state(db_session, other)[:2] == ("bank_confirmed", later.id)

    _retire(db_session, stmt)

    assert _state(db_session, other) == ("bank_confirmed", later.id, Decimal("4.0000"))


def test_retire_with_an_accepted_early_reward(seed, db_session, run):
    """Owner ruling 2026-11-07: an applied `accept_early_reward` no longer blocks the retire. It is retired with the
    statement (status `reverted`, marked, audited); its accrual keeps the early posting date and goes back to
    `posted`, unconfirmed."""
    card = _test_card(seed, db_session)
    _, buy, stmt, line = _early_statement(db_session, run, card)
    action = _accept(db_session, _one_open(db_session, stmt.id, line))
    db_session.commit()
    accepted_on = _accrual_of(db_session, buy).post_on

    _retire(db_session, stmt)

    assert _state(db_session, buy) == ("posted", None, Decimal("10.0000"))
    assert _accrual_of(db_session, buy).post_on == accepted_on
    db_session.expire_all()
    row = db_session.get(ReconciliationAction, action.id)
    assert (row.status, row.inverse.get("retired_with_statement")) == ("reverted", stmt.id)
    audits = db_session.scalars(select(ReconciliationAudit).where(ReconciliationAudit.action_id == action.id,
                                                                  ReconciliationAudit.event == "action_retired")).all()
    assert [a.detail for a in audits] == [{"by": "retire", "action": "accept_early_reward"}]


def test_retire_is_busy_while_a_reward_row_is_locked(pg_engine, seed, db_session, run):
    """The reset takes the bank-wins locks NOWAIT: a held reward row answers 409 `reconcile_busy`, nothing changes."""
    card, rule, buy, stmt = _confirmed(seed, db_session, run)
    row_id = _accrual_of(db_session, buy).ledger_entry_id

    holder = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        holder.execute(select(LedgerEntry).where(LedgerEntry.id == row_id).with_for_update())
        with pytest.raises(CodedConflictError) as info:
            retire_svc.retire_statement(db_session, account_id=card.id, statement_id=stmt.id, note="重複",
                                        label="owner-test", request_id=None)
        db_session.rollback()
    finally:
        holder.rollback()
        holder.close()
    assert info.value.code == "reconcile_busy"
    assert _state(db_session, buy) == ("bank_confirmed", stmt.id, Decimal("9.0000"))
