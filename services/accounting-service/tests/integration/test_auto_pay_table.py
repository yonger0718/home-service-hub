from datetime import date, timedelta
from decimal import Decimal
import pytest
from sqlalchemy.exc import IntegrityError
from app.models import StatementAutoPay
from tests.integration.statement_fixtures import OCT, SEP, _card, _line, _submit, run  # noqa: F401

DAY = date(2026, 9, 3)

def _row(stmt, card, bank, pay_date, status="booked", **extra):
    return StatementAutoPay(statement_id=stmt.id, card_account_id=card.id, pay_account_id=bank.id, amount=Decimal("580"),
                            due_date=pay_date, pay_date=pay_date, status=status, settings_version=1, **extra)

def test_one_booked_pair_per_card_per_20_days(seed, db_session, run):
    bank = seed.account("薪轉"); card = _card(seed, db_session, auto_pay_account_id=bank.id)
    a, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=date(2026, 10, 24))
    b, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 3), "全聯", 580)], period=OCT, due_date=date(2026, 11, 24))
    db_session.add(_row(a, card, bank, date(2026, 10, 27))); db_session.commit()
    db_session.add(_row(b, card, bank, date(2026, 10, 27) + timedelta(days=19)))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
    db_session.add(_row(b, card, bank, date(2026, 10, 27) + timedelta(days=20))); db_session.commit()  # 20 days: no overlap
    assert db_session.query(StatementAutoPay).count() == 2

def test_retired_rows_do_not_block_and_statement_is_unique(seed, db_session, run):
    bank = seed.account("薪轉"); card = _card(seed, db_session, auto_pay_account_id=bank.id)
    a, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=date(2026, 10, 24))
    b, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 3), "全聯", 580)], period=OCT, due_date=date(2026, 10, 27))
    db_session.add(_row(a, card, bank, date(2026, 10, 27), status="retired", reason="twin")); db_session.commit()
    db_session.add(_row(b, card, bank, date(2026, 10, 28))); db_session.commit()  # a retired row never collides
    db_session.add(_row(b, card, bank, date(2026, 12, 1), status="skipped", reason="cutoff"))
    with pytest.raises(IntegrityError):  # statement_id UNIQUE
        db_session.flush()
    db_session.rollback()
