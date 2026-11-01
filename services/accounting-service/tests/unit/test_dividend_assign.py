"""Dividend matching (portfolio integration dividends, R1): the dividend line rule and the pure assignment of a
statement's dividend lines to confirmed receipts. Fictional amounts only."""
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.services import dividend_service as ds
from app.services.portfolio_client import Dividend
from app.services.statements import matching

RULES = matching.Rules(period_end=date(2026, 9, 30))


def _line(id, day, flow, merchant="現金股利", kind="deposit"):
    return matching.Line(id=id, event_id=100 + id, posted_date=day, txn_date=None, flow=Decimal(flow), foreign_amount=None,
                         foreign_currency=None, line_kind=kind, merchant_norm=merchant, installment_seq=None,
                         installment_total=None)


def _div(id, day, amount):
    return Dividend(id, "9999", day, Decimal(amount), "TWD", f"dividend:{id}")


def test_a_dividend_line_is_a_deposit_naming_a_label_and_no_settlement_label():
    assert matching.is_dividend_line(_line(1, date(2026, 9, 12), "990"), RULES)
    assert not matching.is_dividend_line(_line(1, date(2026, 9, 12), "-990", kind="withdrawal"), RULES)
    assert not matching.is_dividend_line(_line(1, date(2026, 9, 12), "990", merchant="交割股款 股利"), RULES)
    assert not matching.is_dividend_line(_line(1, date(2026, 9, 12), "990", merchant="薪資"), RULES)
    assert matching.is_dividend_line(_line(1, date(2026, 9, 12), "990", merchant="配息"),
                                     replace(RULES, dividend_labels=("配息",)))


def test_assign_takes_the_exact_net_amount_within_three_days():
    lines = [_line(1, date(2026, 9, 12), "990")]
    rows = [_div(7, date(2026, 9, 15), "990"), _div(8, date(2026, 9, 16), "990"), _div(9, date(2026, 9, 12), "989")]
    assert ds.assign(lines, rows, set(), RULES) == ds.Assignment(unique={1: rows[0]}, ambiguous={})


def test_reserved_receipts_are_skipped_and_several_are_ambiguous():
    lines = [_line(1, date(2026, 9, 12), "990")]
    rows = [_div(7, date(2026, 9, 11), "990"), _div(8, date(2026, 9, 13), "990")]
    assert ds.assign(lines, rows, {"dividend:7"}, RULES) == ds.Assignment(unique={1: rows[1]}, ambiguous={})
    assert ds.assign(lines, rows, set(), RULES) == ds.Assignment(unique={}, ambiguous={1: (rows[0], rows[1])})


def test_one_receipt_near_two_lines_is_ambiguous_on_both():
    lines = [_line(1, date(2026, 9, 12), "990"), _line(2, date(2026, 9, 13), "990")]
    rows = [_div(7, date(2026, 9, 12), "990")]
    assert ds.assign(lines, rows, set(), RULES) == ds.Assignment(unique={}, ambiguous={1: (rows[0],), 2: (rows[0],)})


def test_one_near_receipt_of_another_amount_is_a_mismatch_and_two_are_none():
    """Rev 2.2, owner ruling 3: a hint only. Reserved receipts, receipts another line takes, and receipts more than
    three days away are not near."""
    lines = [_line(1, date(2026, 9, 12), "2010")]
    one = [_div(7, date(2026, 9, 11), "2000"), _div(8, date(2026, 9, 16), "2000")]          # 8 is four days away
    assert ds.assign(lines, one, set(), RULES) == ds.Assignment(unique={}, ambiguous={}, near={1: one[0]})
    assert ds.nearest_of(lines[0], one[0]) == {"id": 7, "symbol": "9999", "receipt_date": "2026-09-11",
                                               "amount": "2000.0000", "delta": "+10.0000"}
    assert ds.nearest_of(_line(1, date(2026, 9, 12), "1990"), one[0])["delta"] == "-10.0000"
    two = [_div(7, date(2026, 9, 11), "2000"), _div(9, date(2026, 9, 14), "1500")]
    assert ds.assign(lines, two, set(), RULES) == ds.Assignment(unique={}, ambiguous={})
    assert ds.assign(lines, one, {"dividend:7"}, RULES) == ds.Assignment(unique={}, ambiguous={})
    taken = [_line(1, date(2026, 9, 12), "2010"), _line(2, date(2026, 9, 12), "2000")]
    assert ds.assign(taken, one[:1], set(), RULES) == ds.Assignment(unique={2: one[0]}, ambiguous={})
