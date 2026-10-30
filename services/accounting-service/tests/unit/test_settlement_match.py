from datetime import date
from decimal import Decimal

import pytest

from app.services.portfolio_client import Item, Settlement
from app.services.statements import matching as m
from app.services.statements import settlement_match as sm


R = m.Rules(period_end=date(2026, 9, 30))


def L(i, day, flow, merchant="交割股款", kind="withdrawal"):
    return m.Line(id=i, event_id=100 + i, posted_date=day, txn_date=day, flow=Decimal(flow), foreign_amount=None,
                  foreign_currency=None, line_kind=kind, merchant_norm=merchant, installment_seq=None, installment_total=None)


def S(fp, sd, net, *, td=None, ids=("ct:1",), bookable=True, reason=None, source="statement"):
    items = tuple(Item(i, "buy_settle", Decimal(net) / len(ids)) for i in ids)
    return Settlement(td or sd, sd, source, Decimal(net), Decimal(net), "TWD", bookable, reason, items, fp)


def A(lines, sets, *, fps=(), ids=(), contestants=(), rules=R):
    return sm.assign(lines, sets, reserved_fps=set(fps), reserved_ids=set(ids), contestants=set(contestants), rules=rules)


def test_unique_pair_books_within_two_days():
    a = A([L(1, date(2026, 9, 14), "-12305")], [S("f1", date(2026, 9, 12), "-12305")])
    assert list(a.booked) == [1] and a.ambiguous == {} and a.pending == {}


def test_two_lines_one_amount_two_settlements_are_ambiguous():
    a = A([L(1, date(2026, 9, 14), "-5000"), L(2, date(2026, 9, 16), "-5000")], [S("f1", date(2026, 9, 14), "-5000"), S("f2", date(2026, 9, 15), "-5000", ids=("ct:2",))])
    assert a.booked == {} and set(a.ambiguous) == {1, 2}


def test_a_contestant_blocks_its_look_alike_and_is_never_booked():
    a = A([L(1, date(2026, 9, 14), "-5000"), L(2, date(2026, 9, 15), "-5000")], [S("f1", date(2026, 9, 14), "-5000")], contestants={1})
    assert a.booked == {} and set(a.ambiguous) == {2} and 1 not in a.pending


def test_reserved_and_undated_are_never_candidates():
    sets = [S("f1", date(2026, 9, 14), "-5000"), S("f2", None, "-5000", ids=("ct:2",)), S("f3", date(2026, 9, 14), "-5000", ids=("ct:9", "ct:1"))]
    a = A([L(1, date(2026, 9, 14), "-5000")], sets, fps={"f1"}, ids={"ct:1"})
    assert a.booked == {} and a.pending == {1: None} and a.unknown_dates == 1


def test_a_sole_non_bookable_candidate_is_pending_with_its_reason():
    a = A([L(1, date(2026, 9, 17), "-30000")], [S("f1", date(2026, 9, 17), "-30000", bookable=False, reason="margin_day")])
    assert a.pending == {1: "margin_day"} and a.booked == {}


def test_non_settlement_lines_are_ignored_and_amount_is_exact():
    a = A([L(1, date(2026, 9, 14), "-5000", merchant="網銀轉帳"), L(2, date(2026, 9, 14), "-5001")], [S("f1", date(2026, 9, 14), "-5000")])
    assert a.pending == {2: None} and 1 not in a.ambiguous


def test_sum_matching_is_off_by_default_and_gated_when_on():
    lines, sets = [L(1, date(2026, 9, 14), "-8000")], [S("f1", date(2026, 9, 14), "-5000"), S("f2", date(2026, 9, 15), "-3000", ids=("ct:2",))]
    assert A(lines, sets).pending == {1: None}
    on = m.Rules(period_end=date(2026, 9, 30), settlement_sum_matching=True)
    a = A(lines, sets, rules=on)
    assert (a.booked[1].fingerprint, a.booked[1].source_ids, a.booked[1].net_amount) == ("f1+f2", ("ct:1", "ct:2"), Decimal("-8000"))
    assert A(lines + [L(2, date(2026, 9, 14), "-5000")], sets, rules=on).booked == {}           # sum vs single → ambiguous


def test_trade_bounds_past_the_calendar_raise():
    with pytest.raises(sm.CalendarExpired):
        sm.trade_bounds(date(2028, 1, 5), date(2028, 1, 31), extra_holidays=frozenset(), extra_workdays=frozenset())   # past LAST_KNOWN_DAY 2027-12-31


def test_trade_bounds_follow_the_bank_calendar():
    assert sm.trade_bounds(date(2026, 2, 24), date(2026, 3, 23), extra_holidays=frozenset(), extra_workdays=frozenset()) == (date(2026, 2, 13), date(2026, 3, 23))  # 春節: 02-13 is the first trade day settling on or after 02-24
    assert sm.trade_bounds(date(2026, 9, 1), date(2026, 9, 30), extra_holidays=frozenset(), extra_workdays=frozenset()) == (date(2026, 8, 28), date(2026, 9, 30))


def test_with_dates_derives_t_plus_two_only_for_legs_rows():
    rows = sm.with_dates([S("f1", None, "-1", td=date(2026, 10, 7), source="legs"), S("f2", date(2026, 10, 8), "-1", td=date(2026, 10, 6))],
                         extra_holidays=frozenset(), extra_workdays=frozenset())
    assert [r.settlement_date for r in rows] == [date(2026, 10, 12), date(2026, 10, 8)]
