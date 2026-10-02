import uuid
from datetime import date, time
from decimal import Decimal

from app.models import Category, Counterparty, EntryGroup, EntryRewardRule, FxRate, Preference, RewardRule
from tests.helpers import make_account, make_entry


def test_cross_account_listing_filters_by_text_newest_first(client, db_session):
    wallet = make_account(db_session, "錢包")
    card = make_account(db_session, "玉山 UNI")
    older = make_entry(db_session, wallet, "-120", name="午餐", entry_date=date(2026, 9, 1))
    newer = make_entry(db_session, card, "-150", name="午餐", entry_date=date(2026, 9, 2))
    make_entry(db_session, wallet, "-900", name="電費", entry_date=date(2026, 9, 3))
    db_session.commit()

    body = client.get("/entries", params={"q": "午餐"}).json()

    assert body["total"] == 2
    assert [(e["id"], e["account_name"], e["currency"]) for e in body["items"]] == [
        (newer.id, "玉山 UNI", "TWD"), (older.id, "錢包", "TWD"),
    ]


def test_cross_account_listing_filters_by_accounts_kind_dates_and_hides_rewards(client, db_session):
    wallet = make_account(db_session, "錢包", opening="100")
    card = make_account(db_session, "玉山 UNI")
    jpy = make_account(db_session, "去日本的錢", currency="JPY")
    make_entry(db_session, wallet, "-10", entry_date=date(2026, 9, 1))
    make_entry(db_session, card, "-20", entry_date=date(2026, 9, 2))
    make_entry(db_session, card, "5", kind="reward", entry_date=date(2026, 9, 3))
    make_entry(db_session, jpy, "-1800", entry_date=date(2026, 9, 4))
    db_session.commit()

    def amounts(**params):
        return [e["amount"] for e in client.get("/entries", params=params).json()["items"]]

    assert amounts() == ["-1800.0000", "5.0000", "-20.0000", "-10.0000"]
    assert amounts(account_id=[wallet.id, card.id]) == ["5.0000", "-20.0000", "-10.0000"]
    assert amounts(hide_rewards="true") == ["-1800.0000", "-20.0000", "-10.0000"]
    assert amounts(kind="reward") == ["5.0000"]
    assert amounts(date_from="2026-09-02", date_to="2026-09-03") == ["5.0000", "-20.0000"]
    assert amounts(limit=2, offset=1) == ["5.0000", "-20.0000"]
    running = [(e["account_name"], e["running_balance"]) for e in client.get("/entries").json()["items"]]
    assert running == [("去日本的錢", "-1800.0000"), ("玉山 UNI", "-15.0000"), ("玉山 UNI", "-20.0000"), ("錢包", "90.0000")]


def test_list_rows_carry_icons_counterparty_group_rules_and_lock(client, db_session):
    wallet = make_account(db_session, "錢包")
    food = Category(kind="expense", name="飲食", icon="🍜", color="#f0cd92")
    db_session.add(food)
    db_session.flush()
    lunch = Category(kind="expense", parent_id=food.id, name="午餐")
    snack = Category(kind="expense", parent_id=food.id, name="點心", icon="🍰")
    alan = Counterparty(name="Alan")
    group = EntryGroup(kind="split", name="聚餐")
    db_session.add_all([lunch, snack, alan, group])
    db_session.flush()
    rule = RewardRule(account_id=wallet.id, name="國內 1%", method="percent", rate=Decimal("1"), posting="after_window")
    db_session.add(rule)
    db_session.flush()
    meal = make_entry(db_session, wallet, "-230", category_id=lunch.id, group_id=group.id, entry_time=time(12, 0))
    share = make_entry(db_session, wallet, "-180", kind="receivable", counterparty_id=alan.id, group_id=group.id,
                       entry_time=time(12, 0))
    cake = make_entry(db_session, wallet, "-60", category_id=snack.id, source="moze_backup", moze_id="R-9")
    db_session.add(EntryRewardRule(entry_id=meal.id, rule_id=rule.id))
    db_session.commit()

    items = {e["id"]: e for e in client.get("/entries").json()["items"]}

    assert (items[meal.id]["category"], items[meal.id]["category_icon"], items[meal.id]["category_color"]) == (
        "飲食/午餐", "🍜", "#f0cd92",
    )
    assert (items[cake.id]["category_icon"], items[cake.id]["category_color"]) == ("🍰", "#f0cd92")
    assert (items[share.id]["counterparty"], items[share.id]["counterparty_id"]) == ("Alan", alan.id)
    assert items[meal.id]["group"] == {
        "id": group.id, "kind": "split", "name": "聚餐", "count": 2, "total": "-410.0000", "currency": "TWD",
    }
    assert items[meal.id]["rule_names"] == ["國內 1%"]
    assert (items[meal.id]["locked"], items[meal.id]["source"]) == (False, "manual")
    assert (items[cake.id]["locked"], items[cake.id]["moze_id"]) == (True, "R-9")


def test_imported_rows_unlock_after_cutover(client, db_session, monkeypatch):
    wallet = make_account(db_session, "錢包")
    make_entry(db_session, wallet, "-60", source="moze_import")
    db_session.commit()
    assert client.get("/entries").json()["items"][0]["locked"] is True

    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")

    assert client.get("/entries").json()["items"][0]["locked"] is False


def test_entry_detail_includes_children_rules_and_rewards(client, db_session):
    card = make_account(db_session, "玉山 UNI")
    rules = [
        RewardRule(account_id=card.id, name=name, method="percent", rate=Decimal(rate), posting="after_window")
        for name, rate in (("國內 1%", "1"), ("加碼 2%", "2"))
    ]
    db_session.add_all(rules)
    db_session.flush()
    expense = make_entry(db_session, card, "-1000", name="機票", entry_time=time(10, 0))
    fee = make_entry(db_session, card, "-15", kind="fee", parent_entry_id=expense.id, entry_time=time(10, 0))
    reward = make_entry(db_session, card, "30", kind="reward", reward_rule_id=rules[1].id,
                        reward_source_entry_id=expense.id, entry_date=date(2026, 9, 1),
                        posted_date=date(2026, 10, 25))
    db_session.add_all([EntryRewardRule(entry_id=expense.id, rule_id=rule.id) for rule in rules])
    db_session.commit()

    detail = client.get(f"/entries/{expense.id}").json()

    assert [c["id"] for c in detail["children"]] == [fee.id]
    assert [r["name"] for r in detail["rules"]] == ["國內 1%", "加碼 2%"]
    assert detail["rule_names"] == ["國內 1%", "加碼 2%"]
    assert [(r["id"], r["posted_date"]) for r in detail["rewards"]] == [(reward.id, "2026-10-25")]
    assert (detail["open_amount"], detail["is_settled"], detail["refunded_amount"]) == (None, None, "0")
    assert (detail["transfer_counterpart"], detail["settles"], detail["refunds"]) == (None, None, None)
    assert detail["group_members"] == [] and detail["settled_by"] == [] and detail["refunded_by"] == []


def test_entry_detail_links_transfer_settlement_refund_and_group(client, db_session):
    wallet = make_account(db_session, "錢包", opening="1000")
    card = make_account(db_session, "玉山 UNI")
    alan = Counterparty(name="Alan")
    group = EntryGroup(kind="split", name="聚餐")
    db_session.add_all([alan, group])
    db_session.flush()
    pair = uuid.uuid4()
    out_leg = make_entry(db_session, wallet, "-500", kind="transfer_out", transfer_group_id=pair)
    in_leg = make_entry(db_session, card, "500", kind="transfer_in", transfer_group_id=pair)
    lent = make_entry(db_session, card, "-420", kind="receivable", counterparty_id=alan.id, group_id=group.id)
    meal = make_entry(db_session, card, "-230", group_id=group.id)
    collected = make_entry(db_session, wallet, "200", kind="receivable", counterparty_id=alan.id,
                           settles_entry_id=lent.id, is_settlement=True, entry_date=date(2026, 9, 5))
    refund = make_entry(db_session, wallet, "70", kind="refund", refunds_entry_id=meal.id, entry_date=date(2026, 9, 6))
    db_session.commit()

    transfer = client.get(f"/entries/{out_leg.id}").json()
    receivable = client.get(f"/entries/{lent.id}").json()
    collection = client.get(f"/entries/{collected.id}").json()
    refunded = client.get(f"/entries/{meal.id}").json()
    refund_detail = client.get(f"/entries/{refund.id}").json()

    assert transfer["transfer_counterpart"]["id"] == in_leg.id
    assert [m["id"] for m in receivable["group_members"]] == [lent.id, meal.id]
    assert [(s["id"], s["is_settlement"]) for s in receivable["settled_by"]] == [(collected.id, True)]
    assert (receivable["open_amount"], receivable["is_settled"], receivable["is_settlement"]) == ("220.0000", False, False)
    assert (collection["settles"]["id"], collection["is_settlement"]) == (lent.id, True)
    assert (collection["open_amount"], collection["is_settled"]) == (None, None)
    assert [r["id"] for r in refunded["refunded_by"]] == [refund.id]
    assert refunded["refunded_amount"] == "70.0000"
    assert refund_detail["refunds"]["id"] == meal.id


def test_unknown_entry_is_404(client, db_session):
    assert client.get("/entries/999").status_code == 404


def test_month_summary_totals_in_main_currency(client, db_session):
    wallet = make_account(db_session, "錢包")
    jpy = make_account(db_session, "去日本的錢", currency="JPY")
    alan = Counterparty(name="Alan")
    db_session.add_all(
        [alan, FxRate(date=date(2026, 9, 30), base="JPY", quote="TWD", rate=Decimal("0.2"), source="test")]
    )
    db_session.flush()
    september = date(2026, 9, 10)
    make_entry(db_session, wallet, "-1000", entry_date=september)
    make_entry(db_session, wallet, "-15", kind="fee", entry_date=september)
    make_entry(db_session, wallet, "50000", kind="income", entry_date=september)
    make_entry(db_session, wallet, "30", kind="reward", entry_date=september)
    make_entry(db_session, wallet, "20", kind="discount", entry_date=september)
    make_entry(db_session, jpy, "-5000", entry_date=september)
    make_entry(db_session, wallet, "-3000", kind="transfer_out", entry_date=september)
    make_entry(db_session, wallet, "-420", kind="receivable", counterparty_id=alan.id, entry_date=september)
    make_entry(db_session, wallet, "-8", kind="balance_adjustment", entry_date=september)
    make_entry(db_session, wallet, "-999", entry_date=date(2026, 10, 1))
    db_session.commit()

    response = client.get("/entries/summary", params={"month": "2026-09"})

    assert response.status_code == 200
    assert response.json() == {
        "month": "2026-09", "currency": "TWD", "expense": "-2015.0000", "income": "50050.0000", "net": "48035.0000",
        "missing_rates": [],
    }


def test_month_summary_reports_currencies_without_a_rate(client, db_session):
    db_session.add(Preference(main_currency="TWD"))
    usd = make_account(db_session, "美金", currency="USD")
    make_entry(db_session, usd, "-12.5", entry_date=date(2026, 12, 3))
    db_session.commit()

    body = client.get("/entries/summary", params={"month": "2026-12"}).json()

    assert (body["expense"], body["missing_rates"]) == ("0", ["USD"])
    assert client.get("/entries/summary", params={"month": "2026-13"}).status_code == 422
