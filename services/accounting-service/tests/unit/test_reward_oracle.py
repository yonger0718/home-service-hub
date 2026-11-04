from datetime import date
from decimal import Decimal as D

from scripts.reward_oracle import Pair, score


def mk(key=1, **kw):
    base = dict(key=key, rule_id=101, account_id=301, purchase_amount=D("600"), purchase_date=date(2026, 9, 10),
                method="percent", rate=D("1"), fixed_amount=None, unit_spend=None, txn_rounding="keep",
                total_rounding="keep", total_cap=None, shared_cap_id=None, closing_day=None, window_start=date(2026, 9, 1),
                window_end=date(2026, 9, 30), posting="after_transaction", delay_days=0, post_month_offset=0,
                post_day=1, moze_amount=D("6"), moze_date=date(2026, 9, 10))
    base.update(kw)
    return Pair(**base)


def test_amount_bar_counts_uncapped_percent_only():
    r = score([mk(1), mk(2, total_cap=D("3"), moze_amount=D("3"))])
    assert (r.amount_ok, r.amount_total) == (1, 1)
    assert len(r.window_totals) == 1 and r.window_totals[0][4] is True


def test_flagged_and_refunded_excluded_and_counted():
    r = score([mk(1), mk(2, flagged=True), mk(3, refunded=True)])
    assert r.excluded == 2 and r.amount_total == 1


def test_within_one_dollar_counts_ok():
    r = score([mk(1, purchase_amount=D("614.4")), mk(2, moze_amount=D("8"))])
    assert r.amount_ok == 1 and r.amount_total == 2
    assert r.largest[0][0] == 2


def test_posting_scored_separately():
    r = score([mk(1, moze_date=date(2026, 9, 12))])
    assert (r.posting_exact, r.posting_same_period, r.posting_total) == (0, 1, 1)
    assert r.posting_mismatch_by_card == {}
    r2 = score([mk(1, moze_date=date(2026, 10, 12))])
    assert r2.posting_mismatch_by_card == {301: 1}


def test_placeholders_excluded():
    r = score([mk(1, method="fixed", rate=None, fixed_amount=D("0"), moze_amount=D("0")), mk(2)])
    assert r.placeholders_excluded == 1 and r.amount_total == 1


def test_report_header_prints_maps():
    r = score([])
    assert "ROUNDING_MAP" in r.maps and "POSTING_MAP" in r.maps and "after_window" in r.maps


def test_same_period_uses_statement_period():
    # closing day 15: the 10th and the 20th are different statements
    r = score([mk(1, closing_day=15, moze_date=date(2026, 9, 20))])
    assert r.posting_same_period == 0 and r.posting_mismatch_by_card == {301: 1}
    # the 28th and the 2nd of next month are the same statement
    r = score([mk(1, closing_day=15, purchase_date=date(2026, 9, 28), window_start=date(2026, 9, 16),
                  window_end=date(2026, 10, 15), moze_date=date(2026, 10, 2))])
    assert r.posting_same_period == 1 and r.posting_mismatch_by_card == {}


def test_other_methods_outside_amount_bar():
    r = score([mk(1), mk(2, method="fixed", rate=None, fixed_amount=D("5"), moze_amount=D("5"))])
    assert r.amount_total == 1 and r.other_methods == 1


def test_shared_cap_lowest_rule_leads():
    kw = dict(shared_cap_id="x", total_cap=D("100"), purchase_amount=D("125"), moze_amount=D("1.25"))
    r = score([mk(1, rule_id=102, total_rounding="keep", **kw),
               mk(2, rule_id=101, total_rounding="ceil", purchase_date=date(2026, 9, 11), **kw)])
    assert len(r.window_totals) == 1
    rule, _, eng, _, _ = r.window_totals[0]
    assert rule == 101 and eng == D("3")  # 1.25 + 1.25 = 2.5 -> ceil by the lowest rule (keep would give 2.5)


def test_manual_posting_counted():
    r = score([mk(1, posting="manual")])
    assert r.posting_manual == 1 and r.posting_total == 0
