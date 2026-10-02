import uuid
from datetime import date, time, timedelta
from decimal import Decimal

from app.main import app
from app.models import Account, AccountGroup, Category, FxRate, LedgerEntry, Preference, Project, RewardRule
from app.services import ledger_service


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


def test_accounts_list_shows_computed_balances_sorted_by_group_then_order_then_name(client, db_session):
    cards = AccountGroup(name="信用卡", sort_order=2)
    cash = AccountGroup(name="現金", sort_order=1)
    db_session.add_all([cards, cash])
    db_session.flush()
    wallet = _account(db_session, "錢包", opening="2000")
    wallet.group_id = cash.id
    for seq, amount in enumerate(["-120", "-80.5", "500"], start=1):
        _entry(db_session, wallet, amount, seq=seq)
    jpy = _account(db_session, "去日本的錢", currency="JPY", opening="180000")
    jpy.group_id = cash.id
    jpy.sort_order = 1
    _entry(db_session, jpy, "-1500", seq=10)
    card = _account(db_session, "玉山 UNI", opening="0")
    card.group_id = cards.id
    loose = _account(db_session, "Line Bank", opening="5")
    _account(db_session, "舊帳戶", archived=True)
    db_session.commit()

    response = client.get("/accounts")

    assert response.status_code == 200
    body = response.json()
    assert [(a["name"], a["group_name"]) for a in body] == [
        ("錢包", "現金"), ("去日本的錢", "現金"), ("玉山 UNI", "信用卡"), ("Line Bank", None),
    ]
    assert {k: body[0][k] for k in ("id", "currency", "opening_balance", "balance", "entry_count", "group_id")} == {
        "id": wallet.id, "currency": "TWD", "opening_balance": "2000.0000", "balance": "2299.5000",
        "entry_count": 3, "group_id": cash.id,
    }
    assert (body[1]["balance"], body[1]["entry_count"]) == ("178500.0000", 1)
    assert body[3]["id"] == loose.id
    assert set(body[0]) >= {
        "balance_main", "icon", "color", "is_archived", "include_in_total", "is_credit", "closing_day", "due_rule",
        "due_value", "credit_limit", "available_credit", "combined_account_id", "credit_sharing_id",
        "auto_pay_account_id", "fx_fee_pct", "fx_fee_rounding", "fx_fee_refundable", "sort_order",
        "settings_locally_edited", "moze_id", "rule_summaries",
    }


def test_include_archived_lists_archived_accounts(client, db_session):
    _account(db_session, "錢包")
    _account(db_session, "舊帳戶", archived=True)
    db_session.commit()

    assert [a["name"] for a in client.get("/accounts").json()] == ["錢包"]
    listed = client.get("/accounts", params={"include_archived": "true"}).json()
    assert sorted((a["name"], a["is_archived"]) for a in listed) == [("舊帳戶", True), ("錢包", False)]


def test_balance_main_uses_the_latest_cached_rate(client, db_session):
    _account(db_session, "錢包", opening="100")
    _account(db_session, "去日本的錢", currency="JPY", opening="10000")
    _account(db_session, "USDT", currency="USDT", opening="5")
    db_session.add_all(
        [
            FxRate(date=date(2026, 9, 1), base="JPY", quote="TWD", rate=Decimal("0.2"), source="test"),
            FxRate(date=date(2026, 9, 30), base="JPY", quote="TWD", rate=Decimal("0.2163"), source="test"),
        ]
    )
    db_session.commit()

    by_name = {a["name"]: a for a in client.get("/accounts").json()}

    assert by_name["錢包"]["balance_main"] == "100.0000"
    assert by_name["去日本的錢"]["balance_main"] == "2163.0000"
    assert by_name["USDT"]["balance_main"] is None


def test_balance_main_follows_the_preference_main_currency(client, db_session):
    db_session.add(Preference(main_currency="JPY"))
    _account(db_session, "錢包", opening="100")
    db_session.add(FxRate(date=date(2026, 9, 30), base="JPY", quote="TWD", rate=Decimal("0.25"), source="test"))
    db_session.commit()

    [wallet] = client.get("/accounts").json()

    assert wallet["balance_main"] == "400.0000"  # inverse of the cached JPY→TWD rate


def test_available_credit_uses_the_shared_limit_set(client, db_session):
    shared = uuid.uuid4()
    card = _account(db_session, "玉山 UNI", opening="0")
    card.is_credit, card.credit_limit, card.credit_sharing_id = True, Decimal("100000"), shared
    sibling = _account(db_session, "玉山 Pi", opening="0")
    sibling.is_credit, sibling.credit_limit, sibling.credit_sharing_id = True, Decimal("100000"), shared
    solo = _account(db_session, "國泰 CUBE", opening="0")
    solo.is_credit, solo.credit_limit = True, Decimal("50000")
    _entry(db_session, card, "-3000", seq=1)
    _entry(db_session, sibling, "-2000", seq=2)
    _entry(db_session, solo, "-700", seq=3)
    db_session.commit()

    by_name = {a["name"]: a for a in client.get("/accounts").json()}

    assert by_name["玉山 UNI"]["available_credit"] == "95000.0000"
    assert by_name["玉山 Pi"]["available_credit"] == "95000.0000"
    assert by_name["國泰 CUBE"]["available_credit"] == "49300.0000"


def test_rule_summaries_list_enabled_rules_and_detail_lists_all(client, db_session):
    card = _account(db_session, "玉山 UNI", opening="0")
    card.is_credit = True
    card.note = "主力卡"
    db_session.add_all(
        [
            RewardRule(account_id=card.id, name="國內", method="percent", rate=Decimal("1.0000"), posting="after_window", sort_order=1),
            RewardRule(account_id=card.id, name="海外", method="percent", rate=Decimal("2.5000"), posting="after_window", sort_order=2),
            RewardRule(account_id=card.id, name="舊活動", method="fixed", fixed_amount=Decimal("100"), posting="manual", is_enabled=False, sort_order=3),
        ]
    )
    db_session.commit()

    [listed] = client.get("/accounts").json()
    detail = client.get(f"/accounts/{card.id}").json()
    rules = client.get(f"/accounts/{card.id}/reward-rules").json()

    assert listed["rule_summaries"] == ["國內 1%", "海外 2.5%"]
    assert detail["note"] == "主力卡"
    assert [(r["name"], r["is_enabled"]) for r in detail["reward_rules"]] == [("國內", True), ("海外", True), ("舊活動", False)]
    assert rules == detail["reward_rules"]
    assert rules[0]["window"] == "statement_cycle"


def test_unknown_account_detail_and_rules_are_404(client, db_session):
    assert client.get("/accounts/999").status_code == 404
    assert client.get("/accounts/999/reward-rules").status_code == 404


def test_future_posted_entry_excluded_from_balance_but_listed(client, db_session):
    # Review focus 1: a reward posted next month does not move today's balance, but it is listed in
    # canonical order and its running balance counts it only once it is posted.
    today = date.today()
    wallet = _account(db_session, opening="1000")
    spend = LedgerEntry(account_id=wallet.id, kind="expense", amount=Decimal("-100"), currency="TWD",
                        entry_date=today, entry_time=time(9, 0), source="manual")
    reward = LedgerEntry(account_id=wallet.id, kind="reward", amount=Decimal("30"), currency="TWD",
                         entry_date=today, entry_time=time(9, 0), posted_date=today + timedelta(days=35), source="manual")
    db_session.add_all([spend, reward])
    db_session.commit()

    [account] = client.get("/accounts").json()
    items = client.get(f"/accounts/{wallet.id}/entries").json()["items"]

    assert (account["balance"], account["entry_count"]) == ("900.0000", 2)
    assert ledger_service.account_balance(db_session, wallet.id) == Decimal("900")
    assert ledger_service.account_balance(db_session, wallet.id, today + timedelta(days=35)) == Decimal("930")
    assert [(e["kind"], e["posted_date"], e["running_balance"]) for e in items] == [
        ("reward", (today + timedelta(days=35)).isoformat(), "900.0000"),
        ("expense", today.isoformat(), "900.0000"),
    ]


def test_accounts_as_of_excludes_later_posted_entries(client, db_session):
    wallet = _account(db_session, opening="1000")
    _entry(db_session, wallet, "-100", seq=1, day=1)
    _entry(db_session, wallet, "-40", seq=2, day=10)  # posted after as_of
    _entry(db_session, wallet, "30", seq=3, day=1, kind="reward", posted_date=date(2026, 9, 20))  # dated before, posted after
    db_session.commit()

    [as_of] = client.get("/accounts", params={"as_of": "2026-09-05"}).json()
    [current] = client.get("/accounts").json()

    assert (as_of["balance"], as_of["balance_main"], as_of["entry_count"]) == ("900.0000", "900.0000", 3)
    assert (current["balance"], current["entry_count"]) == ("890.0000", 3)
    assert ledger_service.list_accounts(db_session, as_of=date(2026, 9, 5))[0]["balance"] == Decimal("900")
    assert client.get("/accounts", params={"as_of": "2026-09-31"}).status_code == 422


def test_account_detail_as_of_excludes_later_posted_entries(client, db_session):
    card = _account(db_session, "玉山 UNI", opening="0")
    card.is_credit, card.credit_limit = True, Decimal("50000")
    _entry(db_session, card, "-3000", seq=1, day=1)
    _entry(db_session, card, "-700", seq=2, day=15)  # posted after as_of
    db_session.commit()

    detail = client.get(f"/accounts/{card.id}", params={"as_of": "2026-09-10"}).json()
    current = client.get(f"/accounts/{card.id}").json()

    assert (detail["balance"], detail["available_credit"], detail["entry_count"]) == ("-3000.0000", "47000.0000", 2)
    assert (current["balance"], current["available_credit"], current["entry_count"]) == ("-3700.0000", "46300.0000", 2)
    assert ledger_service.get_account(db_session, card.id, as_of=date(2026, 9, 10))["balance"] == Decimal("-3000")
    assert client.get(f"/accounts/{card.id}", params={"as_of": "not-a-date"}).status_code == 422


def test_entries_text_filter_matches_name_merchant_and_description(client, db_session):
    wallet = _account(db_session, opening="0")
    _entry(db_session, wallet, "-1", seq=1, name="午餐便當")
    _entry(db_session, wallet, "-2", seq=2, merchant="午餐店")
    _entry(db_session, wallet, "-3", seq=3, description="和同事吃午餐")
    _entry(db_session, wallet, "-4", seq=4, name="電費")
    _entry(db_session, wallet, "-5", seq=5, name="100%_off")
    db_session.commit()

    by_text = client.get(f"/accounts/{wallet.id}/entries", params={"q": "午餐"}).json()
    by_literal = client.get(f"/accounts/{wallet.id}/entries", params={"q": "%_"}).json()

    assert [e["amount"] for e in by_text["items"]] == ["-3.0000", "-2.0000", "-1.0000"]
    assert by_text["total"] == 3
    assert [e["amount"] for e in by_literal["items"]] == ["-5.0000"]


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


def test_account_summary_totals_entries_posted_in_the_period(client, db_session):
    wallet = _account(db_session, opening="1000")
    other = _account(db_session, "玉山 UNI", opening="0")
    _entry(db_session, wallet, "-100", seq=1, day=1)  # posted before the period
    _entry(db_session, wallet, "-5", seq=2, day=2, kind="fee")
    _entry(db_session, wallet, "3000", seq=3, day=3, kind="income")
    _entry(db_session, wallet, "2", seq=4, day=4, kind="interest")
    _entry(db_session, wallet, "10", seq=5, day=5, kind="discount")
    _entry(db_session, wallet, "30", seq=6, day=1, kind="reward", posted_date=date(2026, 9, 6))  # dated before, posted inside
    _entry(db_session, wallet, "-500", seq=7, day=7, kind="transfer_out")
    _entry(db_session, wallet, "20", seq=8, day=8, kind="refund")
    _entry(db_session, wallet, "-200", seq=9, day=15)
    _entry(db_session, wallet, "-40", seq=10, day=30, posted_date=date(2026, 10, 2))  # posted after the period
    _entry(db_session, other, "-999", seq=11, day=10)
    db_session.commit()

    response = client.get(f"/accounts/{wallet.id}/summary", params={"date_from": "2026-09-02", "date_to": "2026-09-30"})

    assert response.status_code == 200
    assert response.json() == {
        "account_id": wallet.id, "currency": "TWD", "date_from": "2026-09-02", "date_to": "2026-09-30",
        "spend": "-205.0000", "income": "3012.0000", "rewards": "30.0000", "net": "2357.0000",
        "end_balance": "3257.0000", "count": 8,
    }
    summary = ledger_service.period_summary(db_session, wallet.id, date(2026, 9, 2), date(2026, 9, 30))
    assert summary["end_balance"] == ledger_service.account_balance(db_session, wallet.id, as_of=date(2026, 9, 30))


def test_account_summary_is_independent_of_pagination(client, db_session):
    wallet = _account(db_session, opening="0")
    _entry(db_session, wallet, "-100", seq=1, day=1)
    _entry(db_session, wallet, "-50", seq=2, day=2)
    _entry(db_session, wallet, "400", seq=3, day=3, kind="income")
    db_session.commit()
    period = {"date_from": "2026-09-01", "date_to": "2026-09-30"}

    before = client.get(f"/accounts/{wallet.id}/summary", params=period).json()
    page = client.get(f"/accounts/{wallet.id}/entries", params={**period, "limit": 1}).json()
    after = client.get(f"/accounts/{wallet.id}/summary", params=period).json()

    assert (len(page["items"]), page["total"]) == (1, 3)
    assert after == before
    assert (before["count"], before["spend"], before["income"], before["net"]) == (
        3, "-150.0000", "400.0000", "250.0000",
    )


def test_account_summary_unknown_account_is_404_and_bad_range_is_422(client, db_session):
    wallet = _account(db_session)
    db_session.commit()

    period = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
    assert client.get("/accounts/999/summary", params=period).status_code == 404
    reversed_range = {"date_from": "2026-09-30", "date_to": "2026-09-01"}
    assert client.get(f"/accounts/{wallet.id}/summary", params=reversed_range).status_code == 422
    assert client.get(f"/accounts/{wallet.id}/summary", params={"date_from": "2026-09-01"}).status_code == 422
    empty = client.get(f"/accounts/{wallet.id}/summary", params={"date_from": "2026-09-30", "date_to": "2026-09-30"}).json()
    assert (empty["count"], empty["net"], empty["end_balance"]) == (0, "0", "2000.0000")


def test_only_write_endpoint_is_the_import():
    writes = [
        (route.path, sorted(route.methods))
        for route in app.routes
        if getattr(route, "methods", None) and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert writes == [("/imports/moze", ["POST"])]
