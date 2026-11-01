import pytest
from decimal import Decimal
from pydantic import ValidationError
from app.schemas.writes import RewardRuleIn

def _body(**fields):
    body = {"name": " 國內 ", "method": "percent", "rate": "1.5", "posting": "after_window",
            "post_month_offset": 1, "post_day": 15}
    body.update(fields)
    return body

def _fields(exc: ValidationError) -> set[str]:
    return {str(e["loc"][-1]) for e in exc.errors()}

def test_percent_rule_parses_and_strips_name():
    rule = RewardRuleIn(**_body())
    assert (rule.name, rule.rate, rule.fixed_amount, rule.delay_days) == ("國內", Decimal("1.5"), None, 0)
    assert rule.share_cap_with is None and rule.sort_order is None

@pytest.mark.parametrize("fields,field", [
    ({"rate": "0"}, "rate"), ({"rate": "100.0001"}, "rate"), ({"rate": "1.12345"}, "rate"),
    ({"method": "fixed", "rate": None, "fixed_amount": "0"}, "fixed_amount"),
    ({"method": "fixed", "rate": None, "fixed_amount": "1.00001"}, "fixed_amount"),
    ({"total_cap": "0"}, "total_cap"), ({"total_cap": "10000000000000000"}, "total_cap"),
    ({"name": "   "}, "name"), ({"description": "x" * 2001}, "description"), ({"delay_days": 366}, "delay_days"),
    ({"post_month_offset": 3}, "post_month_offset"), ({"post_day": 0}, "post_day"), ({"window": "monthly"}, "window"),
    ({"moze_id": "B-1"}, "moze_id"),
])
def test_rejections_name_the_field(fields, field):
    with pytest.raises(ValidationError) as exc:
        RewardRuleIn(**_body(**fields))
    assert field in _fields(exc.value)

def test_posting_resets_dependent_fields():
    after_txn = RewardRuleIn(**_body(posting="after_transaction", delay_days=3))
    manual = RewardRuleIn(**_body(posting="manual", delay_days=3))
    after_window = RewardRuleIn(**_body(delay_days=9))
    assert (after_txn.post_month_offset, after_txn.post_day, after_txn.delay_days) == (0, 1, 3)
    assert (manual.post_month_offset, manual.post_day, manual.delay_days) == (0, 1, 0)
    assert (after_window.post_month_offset, after_window.post_day, after_window.delay_days) == (1, 15, 0)

@pytest.mark.parametrize("value", [None, "none", 7])
def test_share_cap_with_accepts_null_none_and_rule_id(value):
    assert RewardRuleIn(**_body(share_cap_with=value)).share_cap_with == value

def test_share_cap_with_rejects_other_strings():
    with pytest.raises(ValidationError):
        RewardRuleIn(**_body(share_cap_with="all"))
