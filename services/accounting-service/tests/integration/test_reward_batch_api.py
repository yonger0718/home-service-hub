"""#304: engine reward rows carry `reward_batch` (rule + window + posting day) so the SPA folds them into one row.

Display only: every row stays its own ledger entry; `count` / `total` cover the whole batch, not the loaded page.
"""
from datetime import date
from decimal import Decimal

from app.models import RewardAccrual

WINDOW = (date(2026, 10, 7), date(2026, 11, 6))
POST = date(2026, 11, 6)


def _setup(seed, db, n=3, rules=("甲規則",)):
    card = seed.account("測試卡", is_credit=True)
    points = seed.account("測試點數")
    spends = [seed.entry(card, "-1000", day=date(2026, 10, 10 + i)) for i in range(n)]
    made = []
    for name in rules:
        rule = seed.rule(card, name=name)
        rule.reward_account_id = points.id
        made.append(rule)
    db.flush()
    return card, points, spends, made


def _post(seed, db, points, rule, spend, amount, *, day=POST, state="posted"):
    reward = seed.entry(points, amount, kind="reward", day=day, source="rule",
                        reward_source_entry_id=spend.id, reward_rule_id=rule.id)
    db.flush()
    db.add(RewardAccrual(source_entry_id=spend.id, rule_id=rule.id, window_start=WINDOW[0], window_end=WINDOW[1],
                         post_on=day, estimate=Decimal(amount), amount=Decimal(amount), state=state,
                         ledger_entry_id=reward.id))
    db.flush()
    return reward


def _items(client, path="/entries", **params):
    res = client.get(path, params=params)
    assert res.status_code == 200, res.text
    return {item["id"]: item for item in res.json()["items"]}


def test_engine_reward_rows_carry_their_batch(client, db_session, seed):
    _, points, spends, (rule,) = _setup(seed, db_session)
    rows = [_post(seed, db_session, points, rule, spend, amount) for spend, amount in zip(spends, ("2", "3", "5"))]
    db_session.commit()
    items = _items(client)
    expected = {"rule_id": rule.id, "rule_name": "甲規則", "window_start": "2026-10-07", "window_end": "2026-11-06",
                "entry_date": "2026-11-06", "count": 3, "total": "10.0000", "currency": "TWD"}
    assert [items[row.id]["reward_batch"] for row in rows] == [expected] * 3
    assert all(items[spend.id]["reward_batch"] is None for spend in spends)


def test_batch_counts_the_whole_batch_not_the_page(client, db_session, seed):
    _, points, spends, (rule,) = _setup(seed, db_session)
    for spend in spends:
        _post(seed, db_session, points, rule, spend, "4")
    db_session.commit()
    (item,) = _items(client, limit=1).values()
    assert (item["reward_batch"]["count"], item["reward_batch"]["total"]) == (3, "12.0000")


def test_batches_split_by_rule_and_by_posting_day(client, db_session, seed):
    _, points, spends, (first, second) = _setup(seed, db_session, rules=("甲規則", "乙規則"))
    a = _post(seed, db_session, points, first, spends[0], "1")
    b = _post(seed, db_session, points, first, spends[1], "1")
    late = _post(seed, db_session, points, first, spends[2], "1", day=date(2026, 11, 7))
    other = _post(seed, db_session, points, second, spends[0], "7")
    db_session.commit()
    items = _items(client)
    assert items[a.id]["reward_batch"]["count"] == items[b.id]["reward_batch"]["count"] == 2
    assert (items[late.id]["reward_batch"]["entry_date"], items[late.id]["reward_batch"]["count"]) == ("2026-11-07", 1)
    assert (items[other.id]["reward_batch"]["rule_name"], items[other.id]["reward_batch"]["count"]) == ("乙規則", 1)


def test_bank_confirmed_rows_stay_in_their_batch(client, db_session, seed):
    _, points, spends, (rule,) = _setup(seed, db_session, n=2)
    a = _post(seed, db_session, points, rule, spends[0], "3", state="bank_confirmed")
    _post(seed, db_session, points, rule, spends[1], "4")
    db_session.commit()
    assert _items(client)[a.id]["reward_batch"]["count"] == 2


def test_hand_booked_reward_has_no_batch(client, db_session, seed):
    _, points, spends, _ = _setup(seed, db_session, n=1)
    manual = seed.entry(points, "5", kind="reward", day=POST)
    db_session.commit()
    assert _items(client)[manual.id]["reward_batch"] is None


def test_account_passbook_and_detail_carry_the_batch(client, db_session, seed):
    _, points, spends, (rule,) = _setup(seed, db_session, n=2)
    a = _post(seed, db_session, points, rule, spends[0], "3")
    _post(seed, db_session, points, rule, spends[1], "4")
    db_session.commit()
    passbook = _items(client, f"/accounts/{points.id}/entries")
    assert passbook[a.id]["reward_batch"]["total"] == "7.0000"
    assert client.get(f"/entries/{a.id}").json()["reward_batch"]["count"] == 2
