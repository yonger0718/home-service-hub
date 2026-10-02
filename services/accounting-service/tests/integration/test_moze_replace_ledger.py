from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Account, Category, Counterparty, EntryGroup, EntryRewardRule, LedgerEntry, Project, RewardRule
from app.services.moze_backup_json import parse_backup_doc
from app.services.moze_csv import MozeImportError
from tests.helpers import _entries, _import, _import_backup


def _category_paths(session) -> set[str]:
    categories = {c.id: c for c in session.scalars(select(Category))}
    return {
        f"{c.kind}:{categories[c.parent_id].name}/{c.name}" if c.parent_id else f"{c.kind}:{c.name}"
        for c in categories.values()
    }


def test_opening_balance_entries_and_currency(db_session, moze):
    summary = _import(
        db_session,
        moze.csv(
            moze.opening("去日本的錢", "JPY", "180000"),
            moze.row("去日本的錢", "JPY", "支出", "-1500", main="飲食", sub="午餐"),
        ),
    )
    account = db_session.scalar(select(Account).where(Account.name == "去日本的錢"))
    assert (account.currency, account.opening_balance) == ("JPY", Decimal("180000.0000"))
    [entry] = _entries(db_session, "去日本的錢")
    assert (entry.kind, entry.amount, entry.currency, entry.source) == ("expense", Decimal("-1500.0000"), "JPY", "moze_import")
    assert (entry.original_amount, entry.original_currency, entry.fx_rate, entry.fx_source) == (None, None, None, None)
    assert summary["accounts_created"] == ["去日本的錢"]
    assert summary["accounts"] == [
        {
            "name": "去日本的錢", "currency": "JPY", "opening_balance": "180000.0000", "entry_count": 1,
            "balance": "178500.0000", "converted_entry_count": 0, "converted_amount": "0.0000",
        }
    ]
    assert summary["kind_counts"] == {"expense": 1}


def test_fee_and_discount_columns_become_children_right_after_parent(db_session, moze):
    _import(
        db_session,
        moze.csv(
            moze.opening("信用卡", "TWD", "0"),
            moze.row("信用卡", "TWD", "支出", "-266", fee="-3", discount="10", main="飲食"),
            moze.row("信用卡", "TWD", "支出", "-50", main="飲食"),
        ),
    )
    expense, fee, discount, second = _entries(db_session, "信用卡")
    assert (expense.kind, expense.amount) == ("expense", Decimal("-266.0000"))
    assert (fee.kind, fee.amount, fee.parent_entry_id) == ("fee", Decimal("-3.0000"), expense.id)
    assert (discount.kind, discount.amount, discount.parent_entry_id) == ("discount", Decimal("10.0000"), expense.id)
    assert [expense.seq + 1, expense.seq + 2, expense.seq + 3] == [fee.seq, discount.seq, second.seq]
    assert (fee.entry_date, fee.entry_time) == (expense.entry_date, expense.entry_time)
    assert db_session.get(Category, fee.category_id).name == "手續費"
    assert sum(e.amount for e in (expense, fee, discount)) == Decimal("-259")


def test_categories_projects_and_refund(db_session, moze):
    _import(
        db_session,
        moze.csv(
            moze.opening("錢包", "TWD", "0"),
            moze.row("錢包", "TWD", "應收款項", "-100", main="應收款項", sub="代付", project="日本行"),
            moze.row("錢包", "TWD", "應收款項", "-200", main="應收款項", sub="報帳"),
            moze.row("錢包", "TWD", "紅利回饋", "5", main="紅利回饋", sub="紅利回饋"),
            moze.row("錢包", "TWD", "收入", "1000", main="收入", sub="收入"),
            moze.row("錢包", "TWD", "退款", "350", main="飲食", sub="午餐"),
        ),
    )
    assert _category_paths(db_session) == {
        "receivable:應收款項", "receivable:應收款項/代付", "receivable:應收款項/報帳",
        "reward:紅利回饋", "income:收入", "refund:飲食", "refund:飲食/午餐",
    }
    entries = _entries(db_session, "錢包")
    assert db_session.get(Category, entries[0].category_id).name == "代付"
    assert db_session.get(Project, entries[0].project_id).name == "日本行"
    assert (entries[-1].kind, entries[-1].amount) == ("refund", Decimal("350.0000"))


def test_paired_and_unpaired_transfers(db_session, moze):
    summary = _import(
        db_session,
        moze.csv(
            moze.opening("A", "TWD", "0"),
            moze.opening("B", "TWD", "0"),
            moze.row("A", "TWD", "轉出", "-1000", main="轉帳"),
            moze.row("B", "TWD", "轉入", "1000", main="轉帳"),
            moze.row("A", "TWD", "轉出", "-500", main="轉帳", time="13:00"),
        ),
    )
    out_leg, lonely = _entries(db_session, "A")
    [in_leg] = _entries(db_session, "B")
    assert out_leg.transfer_group_id is not None
    assert out_leg.transfer_group_id == in_leg.transfer_group_id
    assert (out_leg.needs_review, in_leg.needs_review) == (False, False)
    assert (lonely.transfer_group_id, lonely.needs_review) == (None, True)
    assert summary["unpaired_transfers"] == [
        {"row": 6, "account": "A", "kind": "transfer_out", "date": "2026-09-01", "time": "13:00", "amount": "-500.0000", "currency": "TWD"}
    ]
    assert summary["pairing"] == {"pass1": 1, "pass2": 0, "pass3": 0}


def test_reimport_reflects_edits_and_is_idempotent(db_session, moze):
    # Review focus: re-importing must not duplicate entries, categories or projects.
    first = moze.csv(
        moze.opening("錢包", "TWD", "2000"),
        moze.row("錢包", "TWD", "支出", "-120", main="飲食", sub="午餐", project="日常"),
    )
    _import(db_session, first)
    ids_before = (
        db_session.scalar(select(Account.id)),
        sorted(db_session.scalars(select(Category.id))),
        sorted(db_session.scalars(select(Project.id))),
    )
    _import(db_session, first)
    ids_after = (
        db_session.scalar(select(Account.id)),
        sorted(db_session.scalars(select(Category.id))),
        sorted(db_session.scalars(select(Project.id))),
    )
    assert ids_after == ids_before
    assert [e.amount for e in _entries(db_session, "錢包")] == [Decimal("-120.0000")]

    edited = moze.csv(
        moze.opening("錢包", "TWD", "2000"),
        moze.row("錢包", "TWD", "支出", "-150", main="飲食", sub="午餐", project="日常"),
    )
    _import(db_session, edited)
    assert [e.amount for e in _entries(db_session, "錢包")] == [Decimal("-150.0000")]


def test_csv_reimport_never_deletes_rows_without_a_moze_id(db_session, moze):
    _import(
        db_session,
        moze.csv(
            moze.opening("錢包", "TWD", "0"),
            moze.row("錢包", "TWD", "支出", "-1", main="飲食", sub="午餐"),
            moze.row("錢包", "TWD", "支出", "-2", main="娛樂", sub="電影", project="舊專案"),
        ),
    )
    _import(
        db_session,
        moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "支出", "-1", main="飲食", sub="午餐")),
    )
    # Unused rows are swept only when their moze_id left a backup; CSV-created rows have none, so they stay.
    assert _category_paths(db_session) == {"expense:飲食", "expense:飲食/午餐", "expense:娛樂", "expense:娛樂/電影"}
    assert list(db_session.scalars(select(Project.name))) == ["舊專案"]


def test_entries_from_other_sources_are_untouched(db_session, moze):
    _import(db_session, moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "支出", "-1", main="飲食")))
    account = db_session.scalar(select(Account).where(Account.name == "錢包"))
    manual = LedgerEntry(
        account_id=account.id, kind="expense", amount=Decimal("-7"), currency="TWD",
        entry_date=date(2026, 9, 1), source="manual",
    )
    db_session.add(manual)
    db_session.commit()

    _import(db_session, moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "支出", "-2", main="飲食")))
    assert sorted((e.source, e.amount) for e in _entries(db_session, "錢包")) == [
        ("manual", Decimal("-7.0000")),
        ("moze_import", Decimal("-2.0000")),
    ]


def _manual(session, account: Account, amount: str) -> LedgerEntry:
    entry = LedgerEntry(account_id=account.id, kind="expense", amount=Decimal(amount), currency=account.currency,
                        entry_date=date(2026, 9, 20), posted_date=date(2026, 9, 20), source="manual")
    session.add(entry)
    session.flush()
    return entry


def test_csv_import_replaces_a_backup_import(db_session, moze, backup):
    _import_backup(db_session, parse_backup_doc(backup.doc(
        accounts=[backup.account("A-CARD", "華航卡", isCreditAccount=True, startDay=15)],
        rules=[backup.rule("B-1", "A-CARD")],
        records=[backup.record("R-1", "A-CARD", price=-100), backup.record("R-2", "A-CARD", price=-50)],
        packages=[backup.package("PK-1", ["R-1", "R-2"])],
    )))
    card = db_session.scalar(select(Account).where(Account.name == "華航卡"))
    rule = db_session.scalar(select(RewardRule))
    first, second = _manual(db_session, card, "-7"), _manual(db_session, card, "-8")
    db_session.add(EntryRewardRule(entry_id=first.id, rule_id=rule.id))
    db_session.commit()

    _import(db_session, moze.csv(moze.opening("華航卡", "TWD", "0"), moze.row("華航卡", "TWD", "支出", "-30", main="飲食")))

    assert sorted((e.source, e.amount) for e in _entries(db_session, "華航卡")) == [
        ("manual", Decimal("-8.0000")), ("manual", Decimal("-7.0000")), ("moze_import", Decimal("-30.0000")),
    ]
    assert db_session.scalar(select(func.count()).select_from(EntryGroup)) == 0
    assert db_session.scalar(select(RewardRule.id)) == rule.id
    assert db_session.scalar(select(EntryRewardRule)).entry_id == first.id
    assert second.id in {e.id for e in _entries(db_session, "華航卡")}


def test_account_settings_survive_a_csv_reimport(db_session, moze):
    _import(db_session, moze.csv(moze.opening("華航卡", "TWD", "0")))
    card = db_session.scalar(select(Account).where(Account.name == "華航卡"))
    card.is_credit, card.closing_day = True, 15
    db_session.commit()

    _import(db_session, moze.csv(moze.opening("華航卡", "TWD", "-500")))

    db_session.refresh(card)
    assert (card.is_credit, card.closing_day, card.opening_balance) == (True, 15, Decimal("-500.0000"))


def test_csv_currency_change_refused_while_entries_remain(db_session, moze):
    _import(db_session, moze.csv(moze.opening("錢包", "TWD", "0")))
    _manual(db_session, db_session.scalar(select(Account)), "-1")
    db_session.commit()
    with pytest.raises(MozeImportError, match="account '錢包': currency TWD → JPY refused"):
        _import(db_session, moze.csv(moze.opening("錢包", "JPY", "0")))
    db_session.rollback()
    _import(db_session, moze.csv(moze.opening("另一個", "TWD", "0")))
    _import(db_session, moze.csv(moze.opening("另一個", "JPY", "0")))
    assert db_session.scalar(select(Account.currency).where(Account.name == "另一個")) == "JPY"


def test_counterparty_text_becomes_rows_and_posted_date_defaults(db_session, moze):
    _import(db_session, moze.csv(
        moze.opening("錢包", "TWD", "0"),
        moze.row("錢包", "TWD", "應收款項", "-100", main="應收款項", counterparty="Alan"),
        moze.row("錢包", "TWD", "應收款項", "-50", main="應收款項", counterparty="Alan"),
    ))
    entries = _entries(db_session, "錢包")
    [alan] = db_session.scalars(select(Counterparty)).all()
    assert alan.name == "Alan"
    assert {e.counterparty_id for e in entries} == {alan.id}
    assert all(e.posted_date == e.entry_date for e in entries)
    _import(db_session, moze.csv(moze.opening("錢包", "TWD", "0")))
    assert [c.name for c in db_session.scalars(select(Counterparty))] == ["Alan"]  # no moze_id: never swept


def test_backup_seeded_settings_are_kept_by_a_csv_import(db_session, moze, backup):
    _import_backup(db_session, parse_backup_doc(backup.doc(
        accounts=[backup.account("A-1", "錢包")], categories=[backup.category()],
        projects=[backup.project("P-1", "日常")], targets=[backup.target("T-1", "Alan")],
    )))
    _import(db_session, moze.csv(moze.opening("錢包", "TWD", "0")))
    assert db_session.scalar(select(func.count()).select_from(Counterparty)) == 1
    assert db_session.scalar(select(Account.moze_id)) == "A-1"
