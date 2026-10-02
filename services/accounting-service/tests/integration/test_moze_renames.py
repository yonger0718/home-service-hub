from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Account, LedgerEntry
from app.services.moze_csv import MozeImportError
from tests.helpers import _import


def _balance(session, name: str) -> Decimal:
    account = session.scalar(select(Account).where(Account.name == name))
    total = session.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.account_id == account.id))
    return account.opening_balance + total


def _file(moze, name: str) -> bytes:
    return moze.csv(moze.opening(name, "TWD", "1000"), moze.row(name, "TWD", "支出", "-200", main="飲食"))


def test_rename_with_mapping_keeps_one_account(db_session, moze):
    _import(db_session, _file(moze, "A"))
    old_id = db_session.scalar(select(Account.id).where(Account.name == "A"))

    summary = _import(db_session, _file(moze, "B"), renames={"A": "B"})

    assert list(db_session.scalars(select(Account.name))) == ["B"]
    assert db_session.scalar(select(Account.id).where(Account.name == "B")) == old_id
    assert _balance(db_session, "B") == Decimal("800")
    assert summary["accounts_renamed"] == [{"from": "A", "to": "B"}]
    assert summary["accounts_created"] == []
    assert summary["accounts_archived"] == []


def test_rename_without_mapping_archives_old_account_with_zero_balance(db_session, moze):
    _import(db_session, _file(moze, "A"))

    summary = _import(db_session, _file(moze, "B"))

    old = db_session.scalar(select(Account).where(Account.name == "A"))
    assert (old.is_archived, old.opening_balance) == (True, Decimal("0.0000"))
    assert _balance(db_session, "A") == Decimal("0")
    assert _balance(db_session, "B") == Decimal("800")
    assert summary["accounts_archived"] == ["A"]
    assert summary["accounts_created"] == ["B"]


def test_archived_account_reappearing_is_unarchived(db_session, moze):
    _import(db_session, _file(moze, "A"))
    _import(db_session, _file(moze, "B"))

    _import(db_session, _file(moze, "A"))

    account = db_session.scalar(select(Account).where(Account.name == "A"))
    assert (account.is_archived, _balance(db_session, "A")) == (False, Decimal("800"))


def test_rename_of_unknown_account_fails(db_session, moze):
    with pytest.raises(MozeImportError, match="account 'X' does not exist"):
        _import(db_session, _file(moze, "B"), renames={"X": "B"})


def test_rename_onto_existing_other_account_fails(db_session, moze):
    _import(db_session, moze.csv(moze.opening("A", "TWD", "0"), moze.opening("B", "TWD", "0")))
    with pytest.raises(MozeImportError, match="account 'B' already exists"):
        _import(db_session, _file(moze, "B"), renames={"A": "B"})
