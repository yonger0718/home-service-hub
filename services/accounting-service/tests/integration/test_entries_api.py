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


def _debts(db_session):
    wallet = make_account(db_session, "錢包", opening="1000")
    alan, bea = Counterparty(name="Alan"), Counterparty(name="Bea")
    db_session.add_all([alan, bea])
    db_session.flush()
    lent = make_entry(db_session, wallet, "-420", kind="receivable", counterparty_id=alan.id, entry_date=date(2026, 9, 1))
    make_entry(db_session, wallet, "200", kind="receivable", counterparty_id=alan.id, settles_entry_id=lent.id,
               is_settlement=True, entry_date=date(2026, 9, 5))
    repaid = make_entry(db_session, wallet, "-100", kind="receivable", counterparty_id=alan.id, entry_date=date(2026, 9, 2))
    make_entry(db_session, wallet, "100", kind="receivable", counterparty_id=alan.id, settles_entry_id=repaid.id,
               is_settlement=True, entry_date=date(2026, 9, 6))
    borrowed = make_entry(db_session, wallet, "300", kind="payable", counterparty_id=alan.id, entry_date=date(2026, 9, 3))
    meal = make_entry(db_session, wallet, "-80", counterparty_id=alan.id, entry_date=date(2026, 9, 4))
    bea_loan = make_entry(db_session, wallet, "-50", kind="receivable", counterparty_id=bea.id, entry_date=date(2026, 9, 7))
    db_session.commit()
    return alan, bea, lent, borrowed, meal, bea_loan


def test_listing_filters_by_counterparty(client, db_session):
    alan, bea, lent, borrowed, meal, bea_loan = _debts(db_session)

    body = client.get("/entries", params={"counterparty_id": bea.id}).json()
    alan_ids = [e["id"] for e in client.get("/entries", params={"counterparty_id": alan.id}).json()["items"]]

    assert (body["total"], [e["id"] for e in body["items"]]) == (1, [bea_loan.id])
    assert len(alan_ids) == 6 and meal.id in alan_ids and bea_loan.id not in alan_ids


def test_listing_open_keeps_unsettled_receivables_and_payables_only(client, db_session):
    alan, bea, lent, borrowed, meal, bea_loan = _debts(db_session)

    body = client.get("/entries", params={"open": "true"}).json()

    # Settlements, fully settled originals and non-debt kinds are excluded; newest first.
    assert (body["total"], [e["id"] for e in body["items"]]) == (3, [bea_loan.id, borrowed.id, lent.id])
    assert client.get("/entries", params={"open": "false"}).json()["total"] == 7


def test_listing_open_composes_with_counterparty_kind_and_pagination(client, db_session):
    alan, bea, lent, borrowed, meal, bea_loan = _debts(db_session)

    def ids(**params):
        return [e["id"] for e in client.get("/entries", params={"open": "true", **params}).json()["items"]]

    assert ids(counterparty_id=alan.id) == [borrowed.id, lent.id]
    assert ids(counterparty_id=alan.id, kind="receivable") == [lent.id]
    assert ids(counterparty_id=alan.id, limit=1, offset=1) == [lent.id]
    assert ids(counterparty_id=alan.id, date_from="2026-09-02") == [borrowed.id]
    assert client.get("/entries", params={"open": "true", "counterparty_id": alan.id}).json()["total"] == 2


def test_listing_open_excludes_closed_originals(client, db_session):
    wallet = make_account(db_session, "錢包", opening="1000")
    alan = Counterparty(name="Alan")
    db_session.add(alan)
    db_session.flush()
    closed = make_entry(db_session, wallet, "-500", kind="receivable", counterparty_id=alan.id, is_closed=True)
    # A partial settlement: amount + settlements != 0, yet MOZE marked the debt settled.
    make_entry(db_session, wallet, "300", kind="receivable", counterparty_id=alan.id, settles_entry_id=closed.id,
               is_settlement=True, entry_date=date(2026, 9, 2))
    still_open = make_entry(db_session, wallet, "-80", kind="receivable", counterparty_id=alan.id,
                            entry_date=date(2026, 9, 3))
    db_session.commit()

    open_ids = [e["id"] for e in client.get("/entries", params={"open": "true"}).json()["items"]]
    all_ids = [e["id"] for e in client.get("/entries", params={"counterparty_id": alan.id}).json()["items"]]

    assert open_ids == [still_open.id]
    assert closed.id in all_ids


def test_list_rows_carry_the_open_amount_of_debt_originals(client, db_session):
    wallet = make_account(db_session, "錢包", opening="1000")
    alan = Counterparty(name="Alan")
    db_session.add(alan)
    db_session.flush()
    lent = make_entry(db_session, wallet, "-420", kind="receivable", counterparty_id=alan.id)
    collected = make_entry(db_session, wallet, "200", kind="receivable", counterparty_id=alan.id,
                           settles_entry_id=lent.id, is_settlement=True, entry_date=date(2026, 9, 2))
    borrowed = make_entry(db_session, wallet, "300", kind="payable", counterparty_id=alan.id)
    closed = make_entry(db_session, wallet, "-500", kind="receivable", counterparty_id=alan.id, is_closed=True)
    make_entry(db_session, wallet, "100", kind="receivable", counterparty_id=alan.id, settles_entry_id=closed.id,
               is_settlement=True, entry_date=date(2026, 9, 3))
    meal = make_entry(db_session, wallet, "-80")
    db_session.commit()

    rows = {e["id"]: e for e in client.get("/entries").json()["items"]}

    assert rows[lent.id]["open_amount"] == "220.0000"
    assert rows[borrowed.id]["open_amount"] == "300.0000"
    assert rows[closed.id]["open_amount"] == "0.0000"
    assert rows[collected.id]["open_amount"] is None
    assert rows[meal.id]["open_amount"] is None
    # The detail reports the same figure, and its nested rows carry theirs.
    detail = client.get(f"/entries/{lent.id}").json()
    assert detail["open_amount"] == "220.0000"
    assert [s["open_amount"] for s in detail["settled_by"]] == [None]


def test_a_settlement_in_another_currency_leaves_the_list_row_open(client, db_session):
    wallet = make_account(db_session, "錢包", opening="1000")
    yen = make_account(db_session, "日幣", currency="JPY")
    alan = Counterparty(name="Alan")
    db_session.add(alan)
    db_session.flush()
    lent = make_entry(db_session, wallet, "-100", kind="receivable", counterparty_id=alan.id)
    make_entry(db_session, yen, "100", kind="receivable", counterparty_id=alan.id, settles_entry_id=lent.id,
               is_settlement=True, entry_date=date(2026, 9, 2))
    db_session.commit()

    open_rows = client.get("/entries", params={"open": "true"}).json()["items"]
    counterparties = {row["name"]: row for row in client.get("/counterparties").json()}

    assert [(e["id"], e["open_amount"]) for e in open_rows] == [(lent.id, "100.0000")]
    assert client.get(f"/entries/{lent.id}").json()["open_amount"] == "100.0000"
    assert counterparties["Alan"]["open_count"] == 1


def test_listing_rejects_a_non_integer_counterparty(client, db_session):
    assert client.get("/entries", params={"counterparty_id": "alan"}).status_code == 422


def test_list_rows_carry_icons_counterparty_group_rules_and_lock(client, db_session):
    wallet = make_account(db_session, "錢包")
    food = Category(kind="expense", name="飲食", icon="🍜", color="#f0cd92")
    db_session.add(food)
    db_session.flush()
    lunch = Category(kind="expense", parent_id=food.id, name="午餐")
    snack = Category(kind="expense", parent_id=food.id, name="點心", icon="🍰")
    alan = Counterparty(name="Alan")
    group = EntryGroup(kind="split", name="聚餐", merchant="鼎泰豐", description="生日")
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
        "id": group.id, "kind": "split", "name": "聚餐", "merchant": "鼎泰豐", "description": "生日", "count": 2,
        "total": "-410.0000", "currency": "TWD",
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
    group = EntryGroup(kind="split", name="聚餐", merchant="鼎泰豐", description="生日")
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
    make_entry(db_session, wallet, "570", kind="refund", entry_date=september)  # a refund reduces the expense
    make_entry(db_session, wallet, "-999", entry_date=date(2026, 10, 1))
    db_session.commit()

    response = client.get("/entries/summary", params={"month": "2026-09"})

    assert response.status_code == 200
    assert response.json() == {
        "month": "2026-09", "currency": "TWD", "expense": "-1445.0000", "income": "50050.0000", "net": "48605.0000",
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


def _calendar_month(db_session, *, hide_rewards: bool):
    db_session.add(Preference(main_currency="TWD", hide_rewards_on_timeline=hide_rewards))
    wallet = make_account(db_session, "錢包")
    card = make_account(db_session, "玉山 UNI")
    alan = Counterparty(name="Alan")
    db_session.add(alan)
    db_session.flush()
    day1, day2, day3 = date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 9)
    make_entry(db_session, wallet, "-1000", entry_date=day1)
    make_entry(db_session, wallet, "-15", kind="fee", entry_date=day1)
    make_entry(db_session, wallet, "200", kind="refund", entry_date=day1)
    make_entry(db_session, wallet, "50000", kind="income", entry_date=day2)
    make_entry(db_session, wallet, "12", kind="interest", entry_date=day2)
    make_entry(db_session, card, "30", kind="reward", entry_date=day2)
    make_entry(db_session, wallet, "-3000", kind="transfer_out", entry_date=day3)
    make_entry(db_session, card, "3000", kind="transfer_in", entry_date=day3)
    lent = make_entry(db_session, wallet, "-420", kind="receivable", counterparty_id=alan.id, entry_date=day3)
    make_entry(db_session, wallet, "420", kind="receivable", counterparty_id=alan.id, settles_entry_id=lent.id,
               is_settlement=True, entry_date=day3)
    make_entry(db_session, wallet, "-999", entry_date=date(2026, 11, 1))
    db_session.commit()


def test_daily_summary_buckets_counted_rows_by_entry_date(client, db_session):
    _calendar_month(db_session, hide_rewards=False)

    response = client.get("/entries/summary/daily", params={"month": "2026-10"})

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "month": "2026-10", "currency": "TWD", "missing_rates": [],
        "days": [
            {"date": "2026-10-02", "expense": "-815.0000", "income": "0.0000", "count": 3},
            {"date": "2026-10-05", "expense": "0.0000", "income": "50042.0000", "count": 3},
        ],
    }
    month = client.get("/entries/summary", params={"month": "2026-10"}).json()
    assert sum(Decimal(d["expense"]) for d in body["days"]) == Decimal(month["expense"])
    assert sum(Decimal(d["income"]) for d in body["days"]) == Decimal(month["income"])


def test_daily_summary_hides_rewards_per_preference(client, db_session):
    _calendar_month(db_session, hide_rewards=True)

    days = client.get("/entries/summary/daily", params={"month": "2026-10"}).json()["days"]

    assert days[1] == {"date": "2026-10-05", "expense": "0.0000", "income": "50012.0000", "count": 2}


def test_daily_summary_skips_currencies_without_a_rate(client, db_session):
    db_session.add(Preference(main_currency="TWD"))
    wallet = make_account(db_session, "錢包")
    jpy = make_account(db_session, "去日本的錢", currency="JPY")
    make_entry(db_session, wallet, "-100", entry_date=date(2026, 12, 3))
    make_entry(db_session, jpy, "-5000", entry_date=date(2026, 12, 3))
    make_entry(db_session, jpy, "-800", entry_date=date(2026, 12, 4))
    db_session.commit()

    body = client.get("/entries/summary/daily", params={"month": "2026-12"}).json()

    assert body["missing_rates"] == ["JPY"]
    assert body["days"] == [{"date": "2026-12-03", "expense": "-100.0000", "income": "0.0000", "count": 1}]


def test_daily_summary_of_an_empty_month_and_invalid_month(client, db_session):
    body = client.get("/entries/summary/daily", params={"month": "2026-07"}).json()

    assert body == {"month": "2026-07", "currency": "TWD", "days": [], "missing_rates": []}
    assert client.get("/entries/summary/daily", params={"month": "2026-13"}).status_code == 422
    assert client.get("/entries/summary/daily", params={"month": "2026-1"}).status_code == 422


def test_month_of_only_excluded_kinds_keeps_zeros_at_four_places(client, db_session):
    wallet = make_account(db_session, "錢包")
    card = make_account(db_session, "玉山 UNI")
    make_entry(db_session, wallet, "-3000", kind="transfer_out", entry_date=date(2026, 8, 4))
    make_entry(db_session, card, "3000", kind="transfer_in", entry_date=date(2026, 8, 4))
    db_session.commit()

    month = client.get("/entries/summary", params={"month": "2026-08"}).json()
    daily = client.get("/entries/summary/daily", params={"month": "2026-08"}).json()

    assert (month["expense"], month["income"], month["net"]) == ("0.0000", "0.0000", "0.0000")
    assert daily["days"] == []


def test_daily_summary_converts_at_the_cached_rate(client, db_session):
    db_session.add_all([
        Preference(main_currency="TWD"),
        FxRate(date=date(2026, 9, 30), base="JPY", quote="TWD", rate=Decimal("0.2134"), source="test"),
    ])
    jpy = make_account(db_session, "去日本的錢", currency="JPY")
    make_entry(db_session, jpy, "-1234", entry_date=date(2026, 10, 3))
    make_entry(db_session, jpy, "-777", entry_date=date(2026, 10, 3))
    make_entry(db_session, jpy, "5000", kind="income", entry_date=date(2026, 10, 4))
    db_session.commit()

    days = client.get("/entries/summary/daily", params={"month": "2026-10"}).json()["days"]
    month = client.get("/entries/summary", params={"month": "2026-10"}).json()

    assert days == [
        {"date": "2026-10-03", "expense": "-429.1474", "income": "0.0000", "count": 2},
        {"date": "2026-10-04", "expense": "0.0000", "income": "1067.0000", "count": 1},
    ]
    assert sum(Decimal(d["expense"]) for d in days) == Decimal(month["expense"])
    assert sum(Decimal(d["income"]) for d in days) == Decimal(month["income"])


def test_daily_summary_counts_rated_currencies_and_lists_unrated_ones(client, db_session):
    db_session.add_all([
        Preference(main_currency="TWD"),
        FxRate(date=date(2026, 12, 1), base="USD", quote="TWD", rate=Decimal("31.5"), source="test"),
    ])
    usd = make_account(db_session, "美金", currency="USD")
    jpy = make_account(db_session, "去日本的錢", currency="JPY")
    make_entry(db_session, usd, "-12.5", entry_date=date(2026, 12, 3))
    make_entry(db_session, jpy, "-5000", entry_date=date(2026, 12, 3))
    db_session.commit()

    body = client.get("/entries/summary/daily", params={"month": "2026-12"}).json()

    assert body["missing_rates"] == ["JPY"]
    assert body["days"] == [{"date": "2026-12-03", "expense": "-393.7500", "income": "0.0000", "count": 1}]
