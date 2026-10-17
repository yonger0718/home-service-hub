from datetime import date
from decimal import Decimal

import pytest

from app.services.statements import derive
from app.services.statements.derive import Header, LineIn


def L(seq, posted, printed, kind="purchase", merchant="全聯", **kw):
    return LineIn(seq=seq, txn_date=kw.get("txn_date"), posted_date=posted, merchant_raw=merchant,
                  printed_amount=Decimal(printed), foreign_amount=kw.get("foreign_amount"), foreign_currency=kw.get("foreign_currency"),
                  line_kind=kind, installment_seq=kw.get("installment_seq"), installment_total=kw.get("installment_total"),
                  is_subtotal=kw.get("is_subtotal", False))


@pytest.mark.parametrize("kind,line_kind,printed,expected", [
    ("card", "purchase", "580", "-580"), ("card", "fee", "15", "-15"), ("card", "interest", "3", "-3"),
    ("card", "installment", "1000", "-1000"), ("card", "payment", "-5000", "5000"), ("card", "refund", "-200", "200"),
    ("card", "reward", "-50", "50"), ("card", "balance_adjustment", "7", "-7"),
    ("bank", "deposit", "30000", "30000"), ("bank", "withdrawal", "-1200", "-1200"), ("bank", "fee", "-15", "-15"),
    ("bank", "interest", "12", "12"), ("bank", "transfer_in", "500", "500"), ("bank", "transfer_out", "-500", "-500"),
])
def test_flow_amount_follows_the_ledger_sign(kind, line_kind, printed, expected):
    assert derive.flow_amount(kind, line_kind, Decimal(printed)) == Decimal(expected)


@pytest.mark.parametrize("kind,line_kind,printed", [
    ("card", "purchase", "-1"), ("card", "payment", "1"), ("card", "refund", "1"), ("bank", "deposit", "-1"),
    ("bank", "withdrawal", "1"), ("card", "purchase", "0"),
])
def test_flow_amount_rejects_sign_inconsistent_lines(kind, line_kind, printed):
    with pytest.raises(derive.SignError):
        derive.flow_amount(kind, line_kind, Decimal(printed))


def test_derive_lines_drops_subtotals_and_numbers_twins():
    lines = derive.derive_lines("card", [
        L(1, date(2026, 9, 3), "580"), L(2, date(2026, 9, 3), "580"), L(3, date(2026, 9, 3), "1000", is_subtotal=True),
        L(4, date(2026, 9, 4), "580"),
    ])
    assert [l.seq for l in lines] == [1, 2, 4]
    assert [l.logical_key for l in lines] == ["2026-09-03|-580.0000|0", "2026-09-03|-580.0000|1", "2026-09-04|-580.0000|0"]
    assert lines[0].canonical_key == lines[1].canonical_key and lines[0].canonical_key != lines[2].canonical_key
    assert lines[0].flow_amount == Decimal("-580")


def test_canonical_key_ignores_whitespace_case_but_not_merchant_change():
    a = derive.derive_lines("card", [L(1, date(2026, 9, 3), "580", merchant="PAYPAL *Spotify")])[0]
    b = derive.derive_lines("card", [L(1, date(2026, 9, 3), "580", merchant="paypal  * SPOTIFY")])[0]
    c = derive.derive_lines("card", [L(1, date(2026, 9, 3), "580", merchant="PAYPAL *Netflix")])[0]
    assert a.canonical_key == b.canonical_key != c.canonical_key


def _hdr(total, opening=None, currency="TWD", start=date(2026, 9, 1), end=date(2026, 9, 30)):
    return Header(period_start=start, period_end=end, closing_date=end, due_date=None,
                  opening_balance=Decimal(opening) if opening is not None else None, statement_total=Decimal(total),
                  minimum_payment=None, currency=currency)


def test_card_equation_total_equals_opening_minus_flow():
    lines = derive.derive_lines("card", [L(1, date(2026, 9, 3), "580"), L(2, date(2026, 9, 10), "-500", kind="payment")])
    ok = derive.guardrails("card", _hdr("1080", opening="1000"), "TWD", lines, closing_day=None)
    assert ok.ok and ok.checks["equation"] and ok.detail["sum_flow"] == "-80.0000"
    bad = derive.guardrails("card", _hdr("1081", opening="1000"), "TWD", lines, closing_day=None)
    assert not bad.ok and not bad.checks["equation"]


def test_card_missing_opening_assumes_zero_and_flags_it():
    lines = derive.derive_lines("card", [L(1, date(2026, 9, 3), "580")])
    res = derive.guardrails("card", _hdr("580"), "TWD", lines, closing_day=None)
    assert res.ok and res.detail["opening_assumed_zero"] is True


def test_bank_equation_requires_opening():
    lines = derive.derive_lines("bank", [L(1, date(2026, 9, 3), "30000", kind="deposit")])
    assert not derive.guardrails("bank", _hdr("30000"), "TWD", lines, closing_day=None).ok
    assert derive.guardrails("bank", _hdr("31000", opening="1000"), "TWD", lines, closing_day=None).ok


@pytest.mark.parametrize("bad", [
    dict(hdr=_hdr("580", currency="USD")),                                             # currency mismatch
    dict(hdr=_hdr("580", start=date(2026, 6, 1))),                                      # period > 62 d
    dict(lines=[L(1, date(2026, 10, 20), "580")]),                                      # posted outside window
    dict(lines=[L(1, date(2026, 9, 3), "580", kind="installment", installment_seq=5, installment_total=3)]),
])
def test_other_guardrails(bad):
    lines = derive.derive_lines("card", bad.get("lines", [L(1, date(2026, 9, 3), "580")]))
    assert not derive.guardrails("card", bad.get("hdr", _hdr("580")), "TWD", lines, closing_day=None).ok


def test_amounts_round_half_up_like_the_ledger():
    assert derive.flow_amount("card", "purchase", Decimal("1.00005")) == Decimal("-1.0001")
    line = derive.derive_lines("card", [L(1, date(2026, 9, 3), "1.00005")])[0]
    assert line.logical_key == "2026-09-03|-1.0001|0"


# --- closing-day guardrail (cards only): period_end and closing_date each within ±3 days of the account's day ---

def _closing(closing_day, end, *, start=None, closing_date=None, kind="card"):
    hdr = Header(period_start=start or end.replace(day=1), period_end=end, closing_date=closing_date, due_date=None,
                 opening_balance=Decimal("0") if kind == "bank" else None, statement_total=Decimal("580"),
                 minimum_payment=None, currency="TWD")
    line = L(1, hdr.period_start, "580" if kind == "card" else "580", kind="purchase" if kind == "card" else "deposit")
    lines = derive.derive_lines(kind, [line])
    return derive.guardrails(kind, hdr, "TWD", lines, closing_day=closing_day)


@pytest.mark.parametrize("closing_day,start,end", [
    (6, date(2026, 8, 7), date(2026, 9, 6)),     # on the day
    (6, date(2026, 8, 7), date(2026, 9, 9)),     # +3: a holiday shift
    (6, date(2026, 8, 7), date(2026, 9, 3)),     # −3
    (31, date(2026, 9, 1), date(2026, 9, 30)),   # day 31 in a 30-day month closes on the 30th
    (30, date(2026, 2, 1), date(2026, 3, 2)),    # day 30 in February closes on the 28th: +2 across the month end
    (1, date(2026, 8, 2), date(2026, 8, 30)),    # −2 to the next month's 1st across the month end
])
def test_closing_day_within_three_days_passes(closing_day, start, end):
    res = _closing(closing_day, end, start=start)
    assert res.ok and res.checks["closing_day"] is True


@pytest.mark.parametrize("closing_day,start,end,days_off", [
    (6, date(2026, 8, 25), date(2026, 9, 24), -12),  # the payment due date read as the closing date
    (23, date(2026, 9, 1), date(2026, 9, 30), 7),    # a calendar month for a card that closes on the 23rd
    (23, date(2026, 10, 1), date(2026, 10, 31), 8),
    (23, date(2026, 1, 1), date(2026, 1, 30), 7),    # a Lunar New Year shift of +7 goes to review
])
def test_closing_day_off_by_more_than_three_days_fails(closing_day, start, end, days_off):
    res = _closing(closing_day, end, start=start)
    assert not res.ok and res.checks["closing_day"] is False
    assert res.detail["closing_day"] == {"expected_day": closing_day, "period_end": end.isoformat(),
                                         "period_end_days_off": days_off, "closing_date": None,
                                         "closing_date_days_off": None}


def test_closing_day_checks_the_closing_date_too():
    res = _closing(6, date(2026, 9, 6), start=date(2026, 8, 7), closing_date=date(2026, 9, 24))
    assert not res.ok and res.detail["closing_day"]["closing_date_days_off"] == -12
    assert res.detail["closing_day"]["period_end_days_off"] == 0


def test_closing_day_checks_period_end_even_with_a_correct_closing_date():
    # the closing date read right, the period as a calendar month: matching windows and identity use period_end
    res = _closing(23, date(2026, 9, 30), start=date(2026, 9, 1), closing_date=date(2026, 9, 23))
    assert not res.ok and res.detail["closing_day"] == {
        "expected_day": 23, "period_end": "2026-09-30", "period_end_days_off": 7, "closing_date": "2026-09-23",
        "closing_date_days_off": 0}


def test_closing_day_passes_when_both_dates_are_right():
    res = _closing(23, date(2026, 9, 24), start=date(2026, 8, 24), closing_date=date(2026, 9, 24))
    assert res.ok and res.checks["closing_day"] is True


def test_no_closing_day_no_check():
    res = _closing(None, date(2026, 9, 30), start=date(2026, 9, 1))
    assert res.ok and "closing_day" not in res.checks and "closing_day" not in res.detail


def test_bank_statements_skip_the_closing_day_check():
    res = _closing(23, date(2026, 9, 30), start=date(2026, 9, 1), kind="bank")
    assert res.ok and "closing_day" not in res.checks and "closing_day" not in res.detail
