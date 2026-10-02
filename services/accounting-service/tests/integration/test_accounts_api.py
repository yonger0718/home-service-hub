from datetime import date, time
from decimal import Decimal

from app.main import app
from app.models import Account, Category, LedgerEntry, Project


def _account(db, name="錢包", currency="TWD", opening="2000", archived=False) -> Account:
    account = Account(name=name, currency=currency, opening_balance=Decimal(opening), is_archived=archived)
    db.add(account)
    db.flush()
    return account


def _entry(db, account, amount, *, seq, kind="expense", day=1, at=time(12, 0), **extra) -> LedgerEntry:
    entry = LedgerEntry(
        account_id=account.id, kind=kind, amount=Decimal(amount), currency=account.currency,
        entry_date=date(2026, 9, day), entry_time=at, source="moze_import", seq=seq, **extra,
    )
    db.add(entry)
    db.flush()
    return entry


def test_accounts_list_shows_computed_balances_sorted_by_entry_count(client, db_session):
    wallet = _account(db_session, "錢包", opening="2000")
    for seq, amount in enumerate(["-120", "-80.5", "500"], start=1):
        _entry(db_session, wallet, amount, seq=seq)
    jpy = _account(db_session, "去日本的錢", currency="JPY", opening="180000")
    _entry(db_session, jpy, "-1500", seq=10)
    _account(db_session, "舊帳戶", archived=True)
    db_session.commit()

    response = client.get("/accounts")

    assert response.status_code == 200
    assert response.json() == [
        {"id": wallet.id, "name": "錢包", "currency": "TWD", "opening_balance": "2000.0000", "balance": "2299.5000", "entry_count": 3},
        {"id": jpy.id, "name": "去日本的錢", "currency": "JPY", "opening_balance": "180000.0000", "balance": "178500.0000", "entry_count": 1},
    ]


def test_entries_newest_first_with_running_balance_category_and_project(client, db_session):
    wallet = _account(db_session, opening="1000")
    food = Category(kind="expense", name="飲食")
    db_session.add(food)
    db_session.flush()
    lunch = Category(kind="expense", parent_id=food.id, name="午餐")
    trip = Project(name="日本行")
    db_session.add_all([lunch, trip])
    db_session.flush()
    first = _entry(db_session, wallet, "-100", seq=1, day=1, category_id=lunch.id, project_id=trip.id, name="便當")
    _entry(db_session, wallet, "-5", seq=2, day=1, kind="fee", parent_entry_id=first.id, category_id=food.id)
    _entry(db_session, wallet, "300", seq=3, day=2, kind="income")
    db_session.commit()

    body = client.get(f"/accounts/{wallet.id}/entries").json()

    assert body["total"] == 3
    assert [(e["amount"], e["running_balance"]) for e in body["items"]] == [
        ("300.0000", "1195.0000"),
        ("-5.0000", "895.0000"),
        ("-100.0000", "900.0000"),
    ]
    oldest = body["items"][-1]
    assert (oldest["category"], oldest["project"], oldest["name"]) == ("飲食/午餐", "日本行", "便當")
    assert body["items"][1]["category"] == "飲食"
    assert body["items"][1]["parent_entry_id"] == oldest["id"]


def test_converted_entry_counts_only_its_account_currency_amount(client, db_session):
    card = _account(db_session, "華航卡", opening="0")
    _entry(
        db_session, card, "-360", seq=1,
        original_amount=Decimal("-1800"), original_currency="JPY", fx_rate=Decimal("0.2"), fx_source="fx_api",
    )
    db_session.commit()

    [account] = client.get("/accounts").json()
    [entry] = client.get(f"/accounts/{card.id}/entries").json()["items"]

    assert account["balance"] == "-360.0000"
    assert (entry["amount"], entry["currency"], entry["running_balance"]) == ("-360.0000", "TWD", "-360.0000")
    assert (entry["original_amount"], entry["original_currency"], entry["fx_rate"], entry["fx_source"]) == (
        "-1800.0000", "JPY", "0.2000000000", "fx_api",
    )


def test_same_minute_entries_paginate_stably(client, db_session):
    wallet = _account(db_session, opening="0")
    for seq in (10, 11, 12):
        _entry(db_session, wallet, f"-{seq}", seq=seq)
    db_session.commit()

    full = [e["id"] for e in client.get(f"/accounts/{wallet.id}/entries").json()["items"]]
    again = [e["id"] for e in client.get(f"/accounts/{wallet.id}/entries").json()["items"]]
    paged = [
        client.get(f"/accounts/{wallet.id}/entries", params={"limit": 1, "offset": offset}).json()["items"][0]["id"]
        for offset in range(3)
    ]
    assert full == again == paged
    assert len(set(paged)) == 3


def test_untimed_entry_sorts_first_in_its_day(client, db_session):
    wallet = _account(db_session, opening="0")
    timed = _entry(db_session, wallet, "-1", seq=1, at=time(8, 0))
    untimed = _entry(db_session, wallet, "-2", seq=2, at=None)
    db_session.commit()

    items = client.get(f"/accounts/{wallet.id}/entries").json()["items"]

    assert [e["id"] for e in items] == [timed.id, untimed.id]  # newest first = reverse canonical
    assert [e["running_balance"] for e in items] == ["-3.0000", "-2.0000"]


def test_filters_do_not_change_running_balance(client, db_session):
    # Review focus: running balance must be the true account balance even on a filtered page.
    wallet = _account(db_session, opening="1000")
    _entry(db_session, wallet, "-100", seq=1, day=1)
    _entry(db_session, wallet, "500", seq=2, day=2, kind="income")
    _entry(db_session, wallet, "-50", seq=3, day=3)
    db_session.commit()

    by_kind = client.get(f"/accounts/{wallet.id}/entries", params={"kind": "expense"}).json()
    by_date = client.get(f"/accounts/{wallet.id}/entries", params={"date_from": "2026-09-02", "date_to": "2026-09-02"}).json()

    assert [(e["amount"], e["running_balance"]) for e in by_kind["items"]] == [("-50.0000", "1350.0000"), ("-100.0000", "900.0000")]
    assert by_kind["total"] == 2
    assert [(e["amount"], e["running_balance"]) for e in by_date["items"]] == [("500.0000", "1400.0000")]


def test_unknown_account_is_404_and_limit_is_capped(client, db_session):
    assert client.get("/accounts/999/entries").status_code == 404
    wallet = _account(db_session)
    db_session.commit()
    assert client.get(f"/accounts/{wallet.id}/entries", params={"limit": 501}).status_code == 422
    assert client.get(f"/accounts/{wallet.id}/entries").json()["limit"] == 50


def test_no_write_endpoints_for_accounts_or_entries():
    writes = [
        (route.path, sorted(route.methods))
        for route in app.routes
        if getattr(route, "methods", None) and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert writes == []
