"""The terms dismiss list: which verified findings and announced blocks a card's entries hide (#287)."""
from __future__ import annotations

from datetime import date

import pytest

from notify.terms_config import CardEntry, Dismissal
from notify.terms_dismiss import Applied, apply_dismissals, site_key
from notify.terms_parse import CardResult, Finding, NextTerms

TODAY = date(2026, 11, 23)
SRC = "https://bank.example/c"
LIVE = frozenset({162, 163})


def _f(rule_id=162, field="starts_on", site="2026-07-01", quote="q") -> Finding:
    return Finding(rule_id=rule_id, field=field, site=site, system="2025-01-01", source=SRC, quote=quote)


def _missing(site="LINE Pay 消費 10% 刷卡金", quote="綁定 LINE Pay 消費即享10%刷卡金回饋") -> Finding:
    return Finding(rule_id=None, field="missing_rule", site=site, system=None, source=SRC, quote=quote)


def _announced(end="2027-03-31") -> NextTerms:
    return NextTerms(announced=True, period=("2027-01-01", end), rules=({"name": "base"},), source=SRC, quote="q")


def _card(status="changed", **kw) -> CardResult:
    base = dict(account_id=101, status=status, current_period_end=None, findings=(), unverified=(),
                next_terms=None, next_terms_unverified_url=None, sources=(), notes="", truncated=())
    base.update(kw)
    return CardResult(**base)


def _entry(*dismiss: Dismissal) -> CardEntry:
    return CardEntry(account_id=101, official="Card A", bank_domains=("bank.example",), tier="precise",
                     dismiss=tuple(dismiss))


def _apply(card: CardResult, *dismiss: Dismissal, today=TODAY, live=LIVE) -> Applied:
    return apply_dismissals(card, _entry(*dismiss), live, today)


# --- site_key: the leading token of the model's text ---

@pytest.mark.parametrize("field, site, key", [
    ("starts_on", "2026-07-01", "2026-07-01"),
    ("starts_on", " 2026-07-01（本期活動）", "2026-07-01"),
    ("ends_on", "2026-07-01 起", "2026-07-01"),
    ("starts_on", "2026/07/01", None),
    ("starts_on", "約 2026-07-01", None),
    ("starts_on", "2026-07-011", None),
    ("starts_on", "2026-13-01", None),
    ("rate", "9", "9"), ("rate", "9.0000", "9"), ("rate", "9%（國內外合計）", "9"), ("rate", "0.50", "0.5"),
    ("total_cap", "60,000", "60000"), ("total_cap", "60000.0000", "60000"), ("fixed_amount", "100 元", "100"),
    ("rate", "約 9%", None), ("rate", "NT$500", None), ("total_cap", "2026-12-31", None), ("rate", "1/2", None),
    # a range, a fraction or a list is not one number: no prefix of it may stand in for the value
    ("rate", "1.5-3%", None), ("rate", "1.5/2", None), ("rate", "9.55-10", None), ("rate", "1.5.2", None),
    ("total_cap", "9,000-12,000", None), ("total_cap", "60,000/年", None), ("rate", "3,5%", None),
    ("rate", "1,2,3", None), ("total_cap", "1,000,000", "1000000"), ("total_cap", "12,34", None),
    ("total_cap", "500 點", "500"), ("total_cap", "500點", "500"), ("rate", "9.", None),
    ("is_basic", "false（基本為每滿 25 元）", "false"), ("is_basic", "TRUE", "true"), ("is_basic", "falsey", None),
    ("is_basic", "否", None),
    ("window", "calendar_month", "calendar_month"), ("window", "calendar_month（日曆月）", "calendar_month"),
    ("method", "Percent", None), ("window", "calendar_monthX", None), ("window", "calendar_month2", None),
    ("window", "calendar_month_", "calendar_month_"),  # a whole token, equal to no pin the loader accepts
    ("total_cap", None, "none"), ("total_cap", "", "none"), ("ends_on", "  ", "none"),
    # the model also spells a missing value out
    ("total_cap", "null（上限僅適用加碼哩）", "none"), ("total_cap", "None", "none"), ("ends_on", "null", "none"),
    ("total_cap", "無上限", "none"), ("total_cap", "nullable", None), ("is_basic", "null", None),
    ("description", "anything", None), ("name", "x", None),
])
def test_site_key(field, site, key):
    assert site_key(field, site) == key


# --- rule fields ---

def test_no_entries_returns_the_same_card():
    card = _card(findings=(_f(),))
    got = _apply(card)
    assert got.card is card and got.hidden == () and got.stale == () and got.hidden_count == 0


def test_pinned_dismissal_hides_the_matching_value_only():
    pin = Dismissal("starts_on", rule=162, value="2026-07-01")
    same, moved = _f(site="2026-07-01（本期）"), _f(site="2027-01-01")
    got = _apply(_card(findings=(same, moved)), pin)
    assert got.hidden == (same,) and got.card.findings == (moved,) and got.card.status == "changed"


def test_pin_needs_the_same_rule_and_field():
    pin = Dismissal("starts_on", rule=162, value="2026-07-01")
    other_rule, other_field = _f(rule_id=163), _f(field="ends_on")
    got = _apply(_card(findings=(other_rule, other_field)), pin)
    assert got.hidden == () and got.card.findings == (other_rule, other_field)


def test_a_site_value_without_the_leading_token_is_shown():
    pin = Dismissal("rate", rule=162, value="9")
    for site in ("約 9%", "九", "9/10"):
        got = _apply(_card(findings=(_f(field="rate", site=site),)), pin)
        assert got.hidden == (), site


def test_number_pin_compares_as_a_number():
    pin = Dismissal("total_cap", rule=162, value="60000")
    got = _apply(_card(findings=(_f(field="total_cap", site="60,000.00"),)), pin)
    assert got.hidden_count == 1


def test_none_pin_matches_a_missing_site_value():
    pin = Dismissal("total_cap", rule=162, value="none")
    blank, given = _f(field="total_cap", site=""), _f(field="total_cap", site="500")
    assert _apply(_card(findings=(blank,)), pin).hidden == (blank,)
    assert _apply(_card(findings=(_f(field="total_cap", site=None),)), pin).hidden_count == 1
    assert _apply(_card(findings=(given,)), pin).hidden == ()


def test_unpinned_dismissal_hides_any_value_of_that_field():
    whole = Dismissal("description", rule=163)
    a, b = _f(rule_id=163, field="description", site="歐洲 5%"), _f(rule_id=163, field="description", site="另一段")
    keep = _f(rule_id=162, field="description", site="x")
    got = _apply(_card(findings=(a, keep, b)), whole)
    assert got.hidden == (a, b) and got.card.findings == (keep,)


def test_bool_pin():
    pin = Dismissal("is_basic", rule=162, value="true")
    assert _apply(_card(findings=(_f(field="is_basic", site="true（官網稱基本哩）"),)), pin).hidden_count == 1
    assert _apply(_card(findings=(_f(field="is_basic", site="false"),)), pin).hidden_count == 0


def test_unverified_findings_are_never_hidden():
    whole = Dismissal("starts_on", rule=162)
    card = _card(status="unchanged", unverified=(_f(),))
    got = _apply(card, whole)
    assert got.card is card and got.hidden == ()


# --- missing_rule ---

def test_missing_rule_needs_every_string():
    both = Dismissal("missing_rule", contains=("LINE Pay", "10%"), until=date(2026, 12, 31))
    assert _apply(_card(findings=(_missing(),)), both).hidden_count == 1
    other = _missing(site="歐洲地區實體消費加碼 5%", quote="歐洲地區 × 5%")
    half = _missing(site="LINE Pay 消費 5%", quote="LINE Pay 5% 回饋")
    got = _apply(_card(findings=(other, half)), both)
    assert got.hidden == ()


def test_missing_rule_matches_across_site_and_quote_nfkc_and_case():
    d = Dismissal("missing_rule", contains=("line pay", "１０％"), until=date(2026, 12, 31))
    f = _missing(site="ＬＩＮＥ Pay 加碼", quote="即享10%刷卡金")
    assert _apply(_card(findings=(f,)), d).hidden == (f,)


def test_missing_rule_dismissal_does_not_touch_rule_findings():
    d = Dismissal("missing_rule", contains=("LINE Pay",), until=date(2026, 12, 31))
    f = _f(field="description", site="LINE Pay 10%", quote="LINE Pay")
    assert _apply(_card(findings=(f,)), d).hidden == ()


# --- until ---

def test_until_applies_on_its_last_day_and_is_stale_the_day_after():
    d = Dismissal("missing_rule", contains=("LINE Pay",), until=date(2026, 12, 31))
    card = _card(findings=(_missing(),))
    last = _apply(card, d, today=date(2026, 12, 31))
    assert last.hidden_count == 1 and last.stale == ()
    after = _apply(card, d, today=date(2027, 1, 1))
    assert after.hidden == () and after.card is card and after.stale == ((d, "expired"),)


def test_until_on_a_rule_field():
    d = Dismissal("starts_on", rule=162, until=date(2026, 11, 22))
    got = _apply(_card(findings=(_f(),)), d)
    assert got.hidden == () and got.stale == ((d, "expired"),)


# --- next_terms ---

def test_next_terms_dismissal_hides_the_block_for_that_period_end():
    d = Dismissal("next_terms", value="2027-03-31")
    got = _apply(_card(status="announced", next_terms=_announced()), d)
    assert got.hidden_next_terms and got.hidden_count == 1
    assert got.card.next_terms is None and got.card.status == "unchanged"


def test_next_terms_with_another_period_end_is_shown():
    d = Dismissal("next_terms", value="2027-03-31")
    card = _card(status="announced", next_terms=_announced("2027-06-30"))
    got = _apply(card, d)
    assert not got.hidden_next_terms and got.card is card


def test_next_terms_not_announced_is_untouched():
    d = Dismissal("next_terms", value="2027-03-31")
    nt = NextTerms(announced=False, period=(None, "2027-03-31"), rules=(), source=None, quote=None)
    card = _card(status="unchanged", next_terms=nt)
    assert _apply(card, d).card is card


def test_next_terms_dismissal_is_stale_once_the_period_ended():
    d = Dismissal("next_terms", value="2027-03-31")
    card = _card(status="announced", next_terms=_announced())
    assert _apply(card, d, today=date(2027, 3, 31)).hidden_next_terms
    got = _apply(card, d, today=date(2027, 4, 1))
    assert not got.hidden_next_terms and got.stale == ((d, "expired"),)


# --- status after hiding ---

def test_all_findings_hidden_becomes_unchanged_not_a_changed_card_without_findings():
    got = _apply(_card(findings=(_f(),)), Dismissal("starts_on", rule=162))
    assert got.card.findings == () and got.card.status == "unchanged"


def test_all_findings_hidden_with_an_announced_block_becomes_announced():
    got = _apply(_card(findings=(_f(),), next_terms=_announced()), Dismissal("starts_on", rule=162))
    assert got.card.status == "announced" and got.card.next_terms is not None


def test_hidden_block_with_findings_left_is_changed():
    got = _apply(_card(status="announced", findings=(_f(),), next_terms=_announced()),
                 Dismissal("next_terms", value="2027-03-31"))
    assert got.card.status == "changed" and got.card.findings == (_f(),)


def test_a_changed_card_without_findings_keeps_its_status_when_nothing_is_hidden():
    card = _card(status="changed")
    got = _apply(card, Dismissal("starts_on", rule=162))
    assert got.card is card and got.card.status == "changed"


def test_identical_findings_are_all_hidden():
    got = _apply(_card(findings=(_f(), _f())), Dismissal("starts_on", rule=162))
    assert len(got.hidden) == 2 and got.card.findings == ()


# --- stale ---

def test_rule_field_dismissal_for_a_rule_that_is_not_live_is_stale():
    gone = Dismissal("starts_on", rule=999)
    live = Dismissal("starts_on", rule=162)
    got = _apply(_card(status="unchanged"), gone, live)
    assert got.stale == ((gone, "rule"),)


def test_expired_wins_over_rule_gone():
    d = Dismissal("starts_on", rule=999, until=date(2026, 1, 1))
    assert _apply(_card(status="unchanged"), d).stale == ((d, "expired"),)


def test_missing_rule_and_next_terms_dismissals_need_no_rule():
    a = Dismissal("missing_rule", contains=("LINE Pay",), until=date(2026, 12, 31))
    b = Dismissal("next_terms", value="2027-03-31")
    assert _apply(_card(status="unchanged"), a, b, live=frozenset()).stale == ()


# --- next_terms pinned on the announced rates and caps ---

def _terms(*rows, end="2027-03-31") -> NextTerms:
    rules = tuple({"name": f"r{i}", "rate": rate, "cap": cap, "notes": None} for i, (rate, cap) in enumerate(rows))
    return NextTerms(announced=True, period=("2027-01-01", end), rules=rules, source=SRC, quote="q")


RATES = (("0.88", None), ("1", "200"), ("3", "888"))


def test_rates_pin_hides_the_same_numbers_in_any_order_and_spelling():
    d = Dismissal("next_terms", value="2027-03-31", rates=RATES)
    card = _card(status="announced", next_terms=_terms(("3.0000", "888"), ("0.88", None), ("1", "200.00")))
    assert _apply(card, d).hidden_next_terms


def test_rates_pin_shows_the_block_when_a_rate_or_cap_moved():
    d = Dismissal("next_terms", value="2027-03-31", rates=RATES)
    for rows in ((("3", "888"), ("0.88", None), ("1", "100")),      # a cap was cut
                 (("2", "888"), ("0.88", None), ("1", "200")),      # a rate was cut
                 (("3", "888"), ("0.88", None)),                    # a row is gone
                 (("3", "888"), ("0.88", None), ("1", "200"), ("1", "200"))):  # a row was added
        card = _card(status="announced", next_terms=_terms(*rows))
        got = _apply(card, d)
        assert not got.hidden_next_terms and got.card is card, rows


def test_rates_pin_counts_repeated_rows():
    d = Dismissal("next_terms", value="2027-03-31", rates=(("1", "200"), ("1", "200")))
    assert _apply(_card(status="announced", next_terms=_terms(("1", "200"), ("1", "200"))), d).hidden_next_terms
    assert not _apply(_card(status="announced", next_terms=_terms(("1", "200"))), d).hidden_next_terms


def test_without_a_rates_pin_only_the_period_end_counts():
    d = Dismissal("next_terms", value="2027-03-31")
    assert _apply(_card(status="announced", next_terms=_terms(("9", None))), d).hidden_next_terms


def test_terms_rates_reads_the_announced_rows():
    from notify.terms_dismiss import terms_rates
    assert terms_rates(_terms(("3.0000", "888"), (None, None), ("0.88", None))) == (
        (None, None), ("0.88", None), ("3", "888"))


# --- review fixes ---

def test_a_blank_site_value_never_matches_a_value_pin():
    pin = Dismissal("total_cap", rule=162, value="500")
    for site in ("", None, "  "):
        assert _apply(_card(findings=(_f(field="total_cap", site=site),)), pin).hidden == (), site
    day = Dismissal("starts_on", rule=162, value="2026-07-01")
    assert _apply(_card(findings=(_f(site=""),)), day).hidden == ()


def test_a_range_is_not_hidden_by_a_pin_on_its_first_digits():
    pin = Dismissal("rate", rule=162, value="1")
    for site in ("1.5-3%", "1.5/2", "1,5-2"):
        assert _apply(_card(findings=(_f(field="rate", site=site),)), pin).hidden == (), site


def test_hiding_the_announced_block_keeps_a_changed_card_without_findings_as_it_was():
    """`changed` with no verified finding is the every-run 「找不到官網來源」 line: an accepted announcement
    must not silence it."""
    card = _card(status="changed", unverified=(_f(),), next_terms=_announced())
    got = _apply(card, Dismissal("next_terms", value="2027-03-31"))
    assert got.hidden_next_terms and got.card.next_terms is None
    assert got.card.status == "changed" and got.card.unverified == card.unverified


def test_a_not_found_card_stays_not_found():
    card = _card(status="not_found", findings=(_f(),), next_terms=_announced())
    got = _apply(card, Dismissal("starts_on", rule=162), Dismissal("next_terms", value="2027-03-31"))
    assert got.hidden_count == 2 and got.card.status == "not_found"
