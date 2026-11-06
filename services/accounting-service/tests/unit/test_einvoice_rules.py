from datetime import date
from decimal import Decimal

from app.services.einvoice_rules import (
    decide,
    is_integral,
    near_tolerance,
    period_bounds,
    period_of,
    seller_tokens,
    shares_token,
)

E = ("entry", 10)
G = ("group", 5)


def test_period_of():
    assert period_of(date(2026, 9, 1)) == "11509"
    assert period_of(date(2026, 10, 31)) == "11509"
    assert period_of(date(2026, 12, 31)) == "11511"
    assert period_of(date(2027, 1, 1)) == "11601"
    assert period_of(date(2026, 2, 28)) == "11501"


def test_period_bounds():
    assert period_bounds("11509") == (date(2026, 9, 1), date(2026, 10, 31))
    assert period_bounds("11601") == (date(2027, 1, 1), date(2027, 2, 28))
    assert period_bounds("11511") == (date(2026, 11, 1), date(2026, 12, 31))


def test_is_integral():
    assert is_integral(Decimal("100.00"))
    assert is_integral(Decimal("100"))
    assert not is_integral(Decimal("20.99"))


def test_near_tolerance():
    assert near_tolerance(Decimal("100")) == 50
    assert near_tolerance(Decimal("1000")) == 200


def test_seller_tokens():
    assert seller_tokens("Example SaaS Inc.") >= {"example", "saas", "inc"}
    assert seller_tokens("範例超商") == {"範例", "例超", "超商", "範例超", "例超商", "範例超商"}
    assert seller_tokens("7-ELEVEN") == {"7", "eleven"}
    assert seller_tokens(None) == frozenset()
    assert seller_tokens("") == frozenset()


def test_shares_token():
    assert shares_token("範例超商股份有限公司", None, "超商午餐")
    assert shares_token("Example SaaS Inc.", "EXAMPLE*SUB")
    assert not shares_token("範例超商", "咖啡")


def test_decide_unique_links():
    assert decide({1: [E]}, {}, {}) == {1: ("link", E)}


def test_decide_contested_single_target():
    out = decide({1: [E], 2: [E]}, {}, {})
    assert out[1][0] == "ambiguous" and out[2][0] == "ambiguous"


def test_decide_several_candidates():
    assert decide({1: [E, G]}, {}, {})[1] == ("ambiguous", None)


def test_decide_family_conflict():
    family = {("group", 5): 5, ("entry", 11): 5}
    out = decide({1: [("group", 5)], 2: [("entry", 11)]}, {}, family)
    assert out[1] == ("ambiguous", None) and out[2] == ("ambiguous", None)


def test_decide_two_members_same_group_not_contested():
    family = {("entry", 11): 5, ("entry", 12): 5}
    out = decide({1: [("entry", 11)], 2: [("entry", 12)]}, {}, family)
    assert out[1] == ("link", ("entry", 11))
    assert out[2] == ("link", ("entry", 12))


def test_decide_near_and_nothing():
    out = decide({1: [], 2: []}, {1: True}, {})
    assert out[1] == ("near", None) and out[2] == ("nothing", None)
