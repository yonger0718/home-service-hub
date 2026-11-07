"""scripts/reward_oracle.load_pairs reads the same closing day and cap as the engine (#297 M12). Synthetic data."""
import uuid
from datetime import date
from decimal import Decimal

from scripts.reward_oracle import load_pairs
from tests.helpers import make_account, make_entry


def test_pairs_use_the_engine_closing_day_and_group_cap(seed, db_session):
    parent = make_account(db_session, "測試母卡", closing_day=15, is_credit=True)
    child = make_account(db_session, "測試子卡", is_credit=True, combined_account_id=parent.id)
    group = uuid.uuid4()
    big = seed.rule(child, "大上限", total_cap=Decimal("200"), shared_cap_id=group)
    small = seed.rule(child, "小上限", total_cap=Decimal("100"), shared_cap_id=group)
    purchase = make_entry(db_session, child, "-1000", entry_date=date(2026, 9, 20))
    for rule, moze in ((big, "m-1"), (small, "m-2")):
        make_entry(db_session, child, "10", kind="reward", entry_date=date(2026, 10, 20), moze_id=moze,
                   reward_source_entry_id=purchase.id, reward_rule_id=rule.id)
    db_session.commit()

    pairs = {p.rule_id: p for p in load_pairs(db_session)}

    assert {r: (p.closing_day, p.window_start, p.window_end) for r, p in pairs.items()} == {
        big.id: (15, date(2026, 9, 16), date(2026, 10, 15)), small.id: (15, date(2026, 9, 16), date(2026, 10, 15))}
    assert {r: p.group_cap for r, p in pairs.items()} == {big.id: Decimal("100"), small.id: Decimal("100")}
