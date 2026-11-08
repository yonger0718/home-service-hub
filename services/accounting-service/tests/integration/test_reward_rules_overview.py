"""GET /reward-rules: every account, rule state, stats (reward rules design §5.3)."""
import time
from datetime import date, timedelta
from decimal import Decimal

from app.models import AccountLogoImage, EntryRewardRule, RewardAccrual
from app.services import ledger_service

TODAY = date(2026, 11, 1)

def _freeze(monkeypatch):
    monkeypatch.setattr(ledger_service, "_today", lambda: TODAY)

def test_overview_groups_by_account_with_states_and_counts(client, db_session, seed, monkeypatch):
    _freeze(monkeypatch)
    card = seed.account("華航卡", is_credit=True, sort_order=1)
    bare = seed.account("玉山卡", is_credit=True, sort_order=2)
    gone = seed.account("舊卡", is_archived=True, sort_order=0)
    seed.rule(card, name="active", starts_on=date(2026, 1, 1), ends_on=None)
    seed.rule(card, name="expired", starts_on=date(2025, 1, 1), ends_on=date(2026, 10, 31))
    seed.rule(card, name="upcoming", starts_on=date(2027, 1, 1), ends_on=None)
    seed.rule(card, name="disabled", enabled=False, ends_on=date(2026, 1, 1))
    seed.rule(card, name="open", starts_on=None, ends_on=None)  # both bounds blank: always active
    seed.rule(gone, name="archived card rule")
    db_session.commit()
    body = client.get("/reward-rules").json()
    assert [a["account_name"] for a in body["accounts"]] == ["華航卡", "玉山卡"]
    assert [(r["name"], r["state"]) for r in body["accounts"][0]["rules"]] == [
        ("active", "active"), ("expired", "expired"), ("upcoming", "upcoming"), ("disabled", "disabled"),
        ("open", "active")]
    assert body["accounts"][1]["rules"] == []
    assert body["counts"] == {"active": 2, "disabled": 1, "expired": 1, "upcoming": 1, "enabled_expired": 1}
    expired_only = client.get("/reward-rules", params={"state": "expired"}).json()
    assert [r["name"] for r in expired_only["accounts"][0]["rules"]] == ["expired"]
    assert expired_only["counts"]["active"] == 2  # counts ignore the filter
    with_archived = client.get("/reward-rules", params={"include_archived_accounts": "true"}).json()
    assert [a["account_name"] for a in with_archived["accounts"]] == ["舊卡", "華航卡", "玉山卡"]
    one = client.get("/reward-rules", params=[("account_id", bare.id)]).json()
    assert [a["account_id"] for a in one["accounts"]] == [bare.id]
    asked_archived = client.get("/reward-rules", params=[("account_id", gone.id)]).json()
    assert [a["account_id"] for a in asked_archived["accounts"]] == [gone.id]  # explicit id wins over the archived default

def test_overview_stats_per_currency_and_12m_boundary(client, db_session, seed, monkeypatch):
    # Review focus 4: two currencies on one rule; a link dated exactly one year ago counts in the 12-month figure,
    # a future-dated one does not.
    _freeze(monkeypatch)
    card = seed.account("華航卡")
    points = seed.account("點數", currency="PTS")
    rule = seed.rule(card, name="R")
    old = seed.entry(card, "-100", day=TODAY - timedelta(days=400))
    edge = seed.entry(card, "-100", day=date(2025, 11, 1))
    recent = seed.entry(card, "-100", day=TODAY)
    future = seed.entry(card, "-100", day=TODAY + timedelta(days=30))  # a future-dated link is not "last 12 months"
    for purchase in (old, edge, recent, future):
        db_session.add(EntryRewardRule(entry_id=purchase.id, rule_id=rule.id))
    seed.entry(card, "3", kind="reward", day=date(2026, 3, 15), reward_rule_id=rule.id)
    seed.entry(points, "120", kind="reward", day=date(2026, 9, 15), reward_rule_id=rule.id)
    seed.entry(points, "80", kind="reward", day=date(2026, 10, 15), reward_rule_id=rule.id)
    db_session.commit()
    stats = client.get("/reward-rules").json()["accounts"][0]["rules"][0]["stats"]
    assert (stats["linked_entries"], stats["linked_entries_12m"], stats["rewards_count"]) == (4, 2, 3)
    assert stats["rewards_total"] == [{"currency": "PTS", "amount": "200.0000"}, {"currency": "TWD", "amount": "3.0000"}]
    assert stats["rewards_last_date"] == "2026-10-15"

def test_overview_without_rewards_has_empty_totals(client, db_session, seed):
    card = seed.account("華航卡")
    seed.rule(card)
    db_session.commit()
    stats = client.get("/reward-rules").json()["accounts"][0]["rules"][0]["stats"]
    assert (stats["rewards_total"], stats["rewards_last_date"], stats["linked_entries"]) == ([], None, 0)

def test_overview_is_grouped_not_per_rule(client, db_session, seed):
    # 20 cards × 5 rules, 3000 links, 1500 reward rows: median of 5 calls well under the reports' 0.5 s gate.
    import random
    rng = random.Random(7)
    cards = [seed.account(f"卡{i}") for i in range(20)]
    rules = [seed.rule(card, name=f"r{j}") for card in cards for j in range(5)]
    purchases = [seed.entry(cards[i % 20], "-100", day=date(2026, 1 + i % 10, 1)) for i in range(3000)]
    db_session.flush()
    db_session.add_all(EntryRewardRule(entry_id=p.id, rule_id=rng.choice(rules).id) for p in purchases)
    for i in range(1500):
        seed.entry(cards[i % 20], "2", kind="reward", day=date(2026, 1 + i % 10, 15), reward_rule_id=rules[i % 100].id)
    db_session.commit()
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        assert client.get("/reward-rules").status_code == 200
        timings.append(time.perf_counter() - started)
    assert sorted(timings)[2] < 0.5, timings


# polish-reward-and-account-settings D1: usage and the card header fields ride on the overview.
def test_overview_usage_matches_reward_usage(client, db_session, seed, monkeypatch):
    _freeze(monkeypatch)
    card = seed.account("測試卡", is_credit=True)
    rule = seed.rule(card, name="一般", window="calendar_month", total_cap=Decimal("500"))
    spend = seed.entry(card, "-1000", day=date(2026, 11, 1))
    db_session.add(RewardAccrual(source_entry_id=spend.id, rule_id=rule.id, window_start=date(2026, 11, 1),
                                 window_end=date(2026, 11, 30), post_on=date(2026, 12, 5), estimate=Decimal("64"),
                                 amount=Decimal("64"), state="estimated"))
    db_session.commit()
    [account] = client.get("/reward-rules").json()["accounts"]
    assert account["usage"] == client.get(f"/accounts/{card.id}/reward-usage").json()
    assert [Decimal(account["usage"][0][k]) for k in ("estimated", "cap", "remaining")] == [64, 500, 436]


def test_overview_usage_ignores_filters(client, db_session, seed, monkeypatch):
    _freeze(monkeypatch)
    card = seed.account("測試卡", is_credit=True)
    miles = seed.account("測試里程")
    seed.rule(card, name="生效", window="calendar_month").reward_account_id = miles.id
    seed.rule(card, name="過期", starts_on=date(2025, 1, 1), ends_on=date(2026, 10, 31))
    db_session.commit()
    full = client.get("/reward-rules").json()["accounts"]
    expected = next(a for a in full if a["account_id"] == card.id)["usage"]
    assert [u["name"] for u in expected] == ["生效"] and expected[0]["reward_is_money"] is False
    for params in ({"state": "expired"}, [("account_id", card.id)]):
        [account] = [a for a in client.get("/reward-rules", params=params).json()["accounts"]
                     if a["account_id"] == card.id]
        assert account["usage"] == expected


def test_overview_usage_empty_without_active_rules(client, db_session, seed, monkeypatch):
    _freeze(monkeypatch)
    card = seed.account("測試卡", is_credit=True)
    seed.account("空卡", is_credit=True)
    seed.rule(card, name="停用", enabled=False)
    db_session.commit()
    body = client.get("/reward-rules").json()
    assert [a["usage"] for a in body["accounts"]] == [[], []]


def test_overview_header_fields_match_accounts(client, db_session, seed):
    card = seed.account("測試卡", is_credit=True, icon="💳", logo="esun")
    plain = seed.account("錢包")
    db_session.add(AccountLogoImage(account_id=card.id, png=b"\x89PNG", sha256="ab" * 32,
                                    source_media_type="image/png", source_bytes=4))
    db_session.commit()
    listed = {a["id"]: a for a in client.get("/accounts").json()}
    for account in client.get("/reward-rules").json()["accounts"]:
        mine = listed[account["account_id"]]
        fields = ("icon", "logo", "logo_hash")
        assert [account[f] for f in fields] == [mine[f] for f in fields]
    assert listed[card.id]["logo_hash"] == "ab" * 8 and listed[plain.id]["logo_hash"] is None


def test_overview_closing_day_is_effective(client, db_session, seed):
    parent = seed.account("主卡", is_credit=True, closing_day=25)
    child = seed.account("附卡", is_credit=True, combined_account_id=parent.id)
    plain = seed.account("錢包")
    db_session.commit()
    days = {a["account_id"]: a["closing_day"] for a in client.get("/reward-rules").json()["accounts"]}
    assert (days[parent.id], days[child.id], days[plain.id]) == (25, 25, None)


def test_overview_usage_batches_windows_per_account(client, db_session, seed, monkeypatch):
    # One batched read for every card: each row counts only its own group's rows inside its own window.
    _freeze(monkeypatch)
    cycle = seed.account("帳單卡", is_credit=True, closing_day=15)  # window 10/16–11/15
    month = seed.account("月曆卡", is_credit=True)                    # window 11/01–11/30
    rules = {cycle.id: seed.rule(cycle, name="帳單"), month.id: seed.rule(month, name="月曆", window="calendar_month")}
    for card, day, amount in ((cycle, date(2026, 10, 20), "5"), (cycle, date(2026, 11, 20), "7"),
                              (month, date(2026, 10, 20), "3"), (month, date(2026, 11, 20), "4")):
        purchase = seed.entry(card, "-100", day=day)
        seed.entry(card, amount, kind="reward", day=day, reward_source_entry_id=purchase.id,
                   reward_rule_id=rules[card.id].id)
    db_session.commit()
    accounts = {a["account_id"]: a for a in client.get("/reward-rules").json()["accounts"]}
    for card, used in ((cycle, 5), (month, 4)):
        assert accounts[card.id]["usage"] == client.get(f"/accounts/{card.id}/reward-usage").json()
        [row] = accounts[card.id]["usage"]
        assert Decimal(row["used"]) == used
    assert (accounts[cycle.id]["usage"][0]["window_start"], accounts[month.id]["usage"][0]["window_start"]) == (
        "2026-10-16", "2026-11-01")
