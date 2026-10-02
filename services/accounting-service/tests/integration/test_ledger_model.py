from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Account, Category, LedgerEntry


def test_account_names_are_unique(db_session):
    db_session.add(Account(name="Line Bank", currency="TWD"))
    db_session.commit()

    db_session.add(Account(name="Line Bank", currency="TWD"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_same_sub_category_name_under_different_main_categories(db_session):
    food = Category(kind="expense", name="飲食")
    social = Category(kind="expense", name="社交")
    db_session.add_all([food, social])
    db_session.flush()
    db_session.add_all(
        [
            Category(kind="expense", parent_id=food.id, name="午餐"),
            Category(kind="expense", parent_id=social.id, name="午餐"),
        ]
    )
    db_session.commit()

    lunches = db_session.query(Category).filter_by(name="午餐").all()
    assert sorted(c.parent_id for c in lunches) == sorted([food.id, social.id])


def test_top_level_category_names_are_unique_per_kind(db_session):
    db_session.add(Category(kind="expense", name="飲食"))
    db_session.commit()

    db_session.add(Category(kind="expense", name="飲食"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_entry_defaults_and_numeric_precision(db_session):
    account = Account(name="錢包", currency="TWD", opening_balance=Decimal("2000"))
    db_session.add(account)
    db_session.flush()
    entry = LedgerEntry(
        account_id=account.id,
        kind="expense",
        amount=Decimal("-80.5"),
        currency="TWD",
        entry_date=date(2026, 9, 1),
        entry_time=time(12, 0),
        source="moze_import",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)

    assert entry.amount == Decimal("-80.5000")
    assert (entry.original_amount, entry.original_currency, entry.fx_rate, entry.fx_source) == (None, None, None, None)
    assert entry.tags == []
    assert entry.needs_review is False
    assert entry.seq >= 1
    assert entry.created_at is not None


def test_converted_entry_keeps_original_amount_currency_and_rate(db_session):
    card = Account(name="華航卡", currency="TWD")
    db_session.add(card)
    db_session.flush()
    entry = LedgerEntry(
        account_id=card.id, kind="expense", amount=Decimal("-360"), currency="TWD",
        original_amount=Decimal("-1800"), original_currency="JPY", fx_rate=Decimal("0.1987662123"), fx_source="fx_api",
        entry_date=date(2026, 7, 10), source="moze_import",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)

    assert (entry.amount, entry.original_amount, entry.original_currency) == (Decimal("-360.0000"), Decimal("-1800.0000"), "JPY")
    assert (entry.fx_rate, entry.fx_source) == (Decimal("0.1987662123"), "fx_api")
