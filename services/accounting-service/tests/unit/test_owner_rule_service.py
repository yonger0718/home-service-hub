"""Owner-rule matching (inbox spec §6.3). Pure; synthetic data only."""
from decimal import Decimal
from types import SimpleNamespace as NS

from app.services import owner_rule_service as ors


def _r(i, pat, *, kind="withdrawal", sign="-", amount=None, enabled=True):
    return NS(id=i, merchant_pattern=pat, line_kind=kind, flow_sign=sign, amount=amount, enabled=enabled)


def test_match_rule_longest_pattern_then_lowest_id():
    rules = [_r(1, "還款"), _r(2, "放款還款"), _r(3, "放款還款")]
    assert ors.match_rule(rules, line_kind="withdrawal", merchant_norm="放款還款 0925", flow_amount=Decimal("-3865")).id == 2


def test_match_rule_respects_kind_sign_amount_enabled():
    m = lambda rules, **kw: ors.match_rule(rules, **{"line_kind": "withdrawal", "merchant_norm": "放款還款", "flow_amount": Decimal("-3865"), **kw})
    assert m([_r(1, "放款還款", kind="purchase")]) is None
    assert m([_r(1, "放款還款", sign="+")]) is None
    assert m([_r(1, "放款還款", amount=Decimal("-3870"))]) is None
    assert m([_r(1, "放款還款", amount=Decimal("-3865.0000"))]).id == 1
    assert m([_r(1, "放款還款", enabled=False)]) is None
    assert m([_r(1, "", )]) is None  # an empty pattern never matches
