"""The ORM models alone (Base.metadata.create_all) build the phase 2a schema.

These tests do not depend on the Alembic migration; test_migration.py checks that the
migration produces the same schema.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Account,
    AccountGroup,
    Category,
    Counterparty,
    EntryGroup,
    EntryRewardRule,
    ImportRun,
    LedgerEntry,
    Preference,
    Project,
    RewardRule,
)

PHASE_2A_TABLES = {
    "account", "account_group", "category", "counterparty", "entry_group", "entry_reward_rule",
    "fx_rate", "import_run", "ledger_entry", "schedule_definition", "schedule_instance", "preference", "project", "reward_rule",
}


@pytest.fixture()
def model_engine(database_factory):
    engine = create_engine(database_factory())
    with engine.begin() as conn:
        conn.execute(text("CREATE SEQUENCE ledger_entry_seq_seq AS BIGINT"))
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def model_session(model_engine):
    with Session(model_engine, autoflush=False) as session:
        yield session


def _wallet(session: Session, **extra) -> Account:
    account = Account(name=extra.pop("name", "錢包"), currency="TWD", **extra)
    session.add(account)
    session.flush()
    return account


def _entry(session: Session, account: Account, amount: str, **extra) -> LedgerEntry:
    entry = LedgerEntry(
        account_id=account.id, kind=extra.pop("kind", "expense"), amount=Decimal(amount), currency=account.currency,
        entry_date=extra.pop("entry_date", date(2026, 10, 1)), source=extra.pop("source", "manual"), **extra,
    )
    session.add(entry)
    session.flush()
    return entry


def test_models_create_every_phase_2a_table(model_engine):
    assert PHASE_2A_TABLES <= set(inspect(model_engine).get_table_names())
    columns = {c["name"] for c in inspect(model_engine).get_columns("ledger_entry")}
    assert "counterparty" not in columns
    assert {
        "posted_date", "counterparty_id", "group_id", "settles_entry_id", "refunds_entry_id", "reward_rule_id",
        "reward_source_entry_id", "invoice_number", "invoice_random", "moze_id", "updated_at", "is_settlement",
    } <= columns


def test_posted_date_defaults_to_entry_date_and_keeps_an_explicit_value(model_session):
    wallet = _wallet(model_session)
    default = _entry(model_session, wallet, "-100", entry_date=date(2026, 9, 30))
    explicit = _entry(model_session, wallet, "50", kind="reward", entry_date=date(2026, 9, 30), posted_date=date(2026, 11, 5))
    model_session.commit()

    assert default.posted_date == date(2026, 9, 30)
    assert explicit.posted_date == date(2026, 11, 5)


def test_new_columns_have_their_defaults(model_session):
    wallet = _wallet(model_session)
    category = Category(kind="expense", name="飲食")
    project = Project(name="生活")
    run = ImportRun(started_at=datetime(2026, 10, 1, tzinfo=timezone.utc), file_name="x.zip", file_sha256="0" * 64, status="running")
    model_session.add_all([category, project, run])
    model_session.commit()

    assert (wallet.sort_order, wallet.include_in_total, wallet.is_credit, wallet.fx_fee_refundable) == (0, True, False, False)
    assert wallet.settings_locally_edited is False
    assert (category.sort_order, category.is_hidden, category.icon, category.color) == (0, False, None, None)
    assert (project.is_archived, project.sort_order) == (False, 0)
    assert (run.kind, run.exported_at) == ("moze_csv", None)


def test_account_check_constraints(model_session):
    wallet = _wallet(model_session)
    model_session.commit()

    wallet.closing_day = 32
    with pytest.raises(IntegrityError, match="ck_account_closing_day"):
        model_session.commit()
    model_session.rollback()

    wallet.combined_account_id = wallet.id
    with pytest.raises(IntegrityError, match="ck_account_combined_not_self"):
        model_session.commit()


def test_new_enum_values_are_accepted(model_session):
    wallet = _wallet(model_session, is_credit=True, due_rule="days_after_closing", fx_fee_rounding="floor")
    entry = _entry(
        model_session, wallet, "-1166", source="moze_backup", moze_id="R-1",
        original_amount=Decimal("-5390"), original_currency="JPY", fx_rate=Decimal("0.2163"), fx_source="manual",
    )
    group = EntryGroup(kind="split", name="聚餐")
    model_session.add(group)
    model_session.flush()
    entry.group_id = group.id
    model_session.commit()

    assert (entry.source, entry.fx_source, wallet.due_rule) == ("moze_backup", "manual", "days_after_closing")


def test_rule_attachment_cascades_with_the_entry_and_protects_the_rule(model_session):
    card = _wallet(model_session, name="玉山 UNI", is_credit=True)
    rule = RewardRule(account_id=card.id, name="國內 1%", method="percent", rate=Decimal("1"), posting="after_window")
    model_session.add(rule)
    model_session.flush()
    entry = _entry(model_session, card, "-1000")
    model_session.add(EntryRewardRule(entry_id=entry.id, rule_id=rule.id))
    model_session.commit()

    assert (rule.window, rule.txn_rounding, rule.total_rounding, rule.is_enabled) == ("statement_cycle", "keep", "keep", True)
    with pytest.raises(IntegrityError):
        model_session.execute(delete(RewardRule).where(RewardRule.id == rule.id))
    model_session.rollback()

    model_session.execute(delete(LedgerEntry).where(LedgerEntry.id == entry.id))
    model_session.commit()
    assert model_session.scalar(select(EntryRewardRule)) is None


def test_deleting_a_settled_target_clears_the_link(model_session):
    wallet = _wallet(model_session)
    alan = Counterparty(name="Alan")
    model_session.add(alan)
    model_session.flush()
    receivable = _entry(model_session, wallet, "-420", kind="receivable", counterparty_id=alan.id)
    collection = _entry(
        model_session, wallet, "200", kind="receivable", counterparty_id=alan.id, settles_entry_id=receivable.id,
        is_settlement=True,
    )
    model_session.commit()

    model_session.execute(delete(LedgerEntry).where(LedgerEntry.id == receivable.id))
    model_session.commit()
    model_session.refresh(collection)
    assert collection.settles_entry_id is None


def test_settlement_sign_requires_is_settlement(model_session):
    wallet = _wallet(model_session)
    model_session.commit()

    # A positive receivable (收款) is a settlement; without the flag the check rejects it.
    with pytest.raises(IntegrityError, match="ck_ledger_entry_settlement_sign"):
        _entry(model_session, wallet, "200", kind="receivable")  # _entry flushes
    model_session.rollback()

    collection = _entry(model_session, wallet, "200", kind="receivable", is_settlement=True)
    model_session.commit()
    assert collection.is_settlement is True
    assert _entry(model_session, wallet, "-50", kind="receivable").is_settlement is False

    # Only receivable and payable rows may carry the flag.
    with pytest.raises(IntegrityError, match="ck_ledger_entry_settlement_sign"):
        _entry(model_session, wallet, "-80", kind="expense", is_settlement=True)


def test_preference_is_a_single_row_with_defaults(model_session):
    model_session.add(Preference())
    model_session.commit()
    pref = model_session.get(Preference, 1)
    assert (pref.expense_income_colors, pref.keypad_layout, pref.week_start, pref.main_currency) == ("red_green", "calculator", 0, "TWD")
    assert (pref.hide_rewards_on_timeline, pref.abbreviate_totals) == (False, True)

    model_session.add(Preference(id=2))
    with pytest.raises(IntegrityError, match="ck_preference_single_row"):
        model_session.commit()


def test_group_and_counterparty_names_are_unique(model_session):
    model_session.add_all([AccountGroup(name="信用卡"), AccountGroup(name="信用卡")])
    with pytest.raises(IntegrityError):
        model_session.commit()
    model_session.rollback()

    model_session.add_all([Counterparty(name="Alan", moze_id=str(uuid.uuid4())), Counterparty(name="Alan")])
    with pytest.raises(IntegrityError):
        model_session.commit()
