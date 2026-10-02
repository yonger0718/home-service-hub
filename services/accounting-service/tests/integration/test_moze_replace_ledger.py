from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import Account, Category, LedgerEntry, Project
from tests.helpers import _entries, _import


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


def test_category_cleanup_keeps_ancestors_and_removes_unused_branches(db_session, moze):
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
    assert _category_paths(db_session) == {"expense:飲食", "expense:飲食/午餐"}
    assert list(db_session.scalars(select(Project.name))) == []


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
