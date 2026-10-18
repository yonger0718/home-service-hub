from decimal import Decimal

import pytest

from tests.worker.composite_fixture import (DECLARED, HOLDINGS, ROWS_59, ROWS_91, L, W, X_BAL, X_IN, X_OUT, amount, header, row,
                               section_header, statement)
from worker import composite
from worker.composite import SplitError


def _split(lines=None, declared=DECLARED):
    return composite.split(lines if lines is not None else statement(), declared)


def test_fixture_splits_into_two_row_sections_and_two_anchors():
    sections, report = _split()
    by_key = {s.key: s for s in sections}
    assert list(by_key) == ["91/TWD", "59/TWD", "68/TWD", "50/JPY"]
    assert len(by_key["91/TWD"].rows) == 16 and len(by_key["59/TWD"].rows) == 12
    assert by_key["91/TWD"].period_start.isoformat() == "2026-09-01" and by_key["91/TWD"].period_end.isoformat() == "2026-09-30"
    assert by_key["91/TWD"].opening_balance == Decimal("30000") and by_key["91/TWD"].closing_balance == Decimal("34431")
    assert by_key["59/TWD"].opening_balance == Decimal("50000") and by_key["59/TWD"].closing_balance == Decimal("52752")
    assert by_key["68/TWD"].rows == () and by_key["68/TWD"].opening_balance == by_key["68/TWD"].closing_balance == Decimal("0")
    assert by_key["50/JPY"].currency == "JPY" and by_key["50/JPY"].closing_balance == Decimal("150000")
    assert report.skipped_keys == ["50/USD"] and report.rows_per_key == {"91/TWD": 16, "59/TWD": 12}
    assert report.sections == 4 and report.anchor == 2


def test_rows_carry_direction_from_the_column_geometry():
    sections, _ = _split()
    first, second = sections[0].rows[0], sections[0].rows[1]
    assert first.withdrawal == Decimal("2000") and first.deposit is None and first.balance == Decimal("28000")
    assert second.deposit == Decimal("1500") and second.withdrawal is None
    assert first.description == "交割股款" and first.currency == "TWD" and first.txn_date.isoformat() == "2026-09-01"


def test_first_row_deposit_is_read_from_geometry():
    rows = [("2026/09/01", "薪資獎金", None, "1,000", "31,000")] + [("2026/09/02", "交割股款", "1,000", None, "30,000")]
    holdings = {**HOLDINGS, "91/TWD": "30,000"}
    sections, _ = _split(statement(rows_91=rows, holdings=holdings, split_at=2))
    assert sections[0].rows[0].deposit == Decimal("1000") and sections[0].opening_balance == Decimal("30000")


def test_report_and_errors_carry_keys_and_counts_only():
    _, report = _split()
    assert "34,431" not in repr(report) and "交割" not in repr(report)
    bad = [("2026/09/01", "交割股款", "2,000", None, "99,999")] + ROWS_91[1:]
    with pytest.raises(SplitError) as info:
        _split(statement(rows_91=bad))
    assert "91/TWD" in str(info.value) and "99,999" not in str(info.value) and "交割" not in str(info.value)


@pytest.mark.parametrize("mutate, message", [
    (lambda lines: [l for l in lines if "綜合月對帳單" not in l.text], "period"),
    (lambda lines: lines[:5] + [lines[4]] + lines[5:], "duplicate holdings"),
])
def test_period_and_duplicate_holdings_fail(mutate, message):
    with pytest.raises(SplitError, match=message):
        _split(mutate(statement()))


def test_balance_chain_violations_fail_closed():
    doctored = [*ROWS_91[:5], ("2026/09/08", "網銀轉帳", None, "2,000", "33,001"), *ROWS_91[6:]]
    with pytest.raises(SplitError, match=r"91/TWD.*row 5"):
        _split(statement(rows_91=doctored))
    with pytest.raises(SplitError, match=r"91/TWD.*closing"):
        _split(statement(holdings={**HOLDINGS, "91/TWD": "34,432"}))


def test_row_shape_violations_fail_closed():
    both = row(3, 110, "2026/09/01", "交割股款", "2,000", "1,000", "28,000")
    neither = row(3, 110, "2026/09/01", "交割股款", None, None, "28,000")
    two_balances = row(3, 110, "2026/09/01", "交割股款", "2,000", None, "28,000", extra=(amount("1", X_BAL + 40),))
    no_currency = L(3, 110, W("2026/09/01", 38), W("交割股款", 90), amount("2,000", X_OUT), amount("28,000", X_BAL))
    trailing_word = row(3, 110, "2026/09/01", "交割股款", "2,000", None, "28,000", extra=(W("x", 600),))
    negative = row(3, 110, "2026/09/01", "交割股款", "-2,000", None, "28,000")
    for bad in (both, neither, two_balances, no_currency, trailing_word, negative):
        lines = statement()
        index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 110)
        lines[index] = bad
        with pytest.raises(SplitError, match="91/TWD"):
            _split(lines)


def test_mixed_currency_and_unknown_currency_fail():
    lines = statement()
    index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 122)
    lines[index] = row(3, 122, *ROWS_91[1][:4], ROWS_91[1][4], cur="JPY")
    with pytest.raises(SplitError, match="currency"):
        _split(lines)


def test_page_number_and_repeated_header_continue_the_section():
    sections, _ = _split(statement(split_at=3))
    assert len(sections[0].rows) == 16  # 3 rows on page 3, 13 after the page number + repeated header on page 4


def test_dated_row_outside_a_section_and_before_a_header_fail():
    lines = statement()
    lines.insert(11, row(3, 60, "2026/09/01", "交割股款", "1", None, "1"))  # after 交易明細, before any section header
    with pytest.raises(SplitError, match="outside"):
        _split(lines)
    lines = statement()
    header_index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 90)
    del lines[header_index]
    with pytest.raises(SplitError, match="geometry"):
        _split(lines)


def test_unknown_line_closes_the_section_and_a_later_row_fails():
    lines = statement()
    index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 134)
    lines.insert(index, L(3, 133, W("本月優惠活動", 40)))
    with pytest.raises(SplitError, match="outside"):
        _split(lines)


def test_declaration_mismatches_fail_and_undeclared_without_rows_is_skipped():
    with pytest.raises(SplitError, match="anchor_only"):
        _split(declared={**DECLARED, "91/TWD": True})
    with pytest.raises(SplitError, match="no section"):
        _split(declared={**DECLARED, "68/TWD": False})
    with pytest.raises(SplitError, match="undeclared"):
        _split(declared={k: v for k, v in DECLARED.items() if k != "59/TWD"})
    with pytest.raises(SplitError, match="holdings"):
        _split(declared={**DECLARED, "77/TWD": True})
    _, report = _split(declared={k: v for k, v in DECLARED.items() if k != "50/JPY"})
    assert sorted(report.skipped_keys) == ["50/JPY", "50/USD"]


def test_same_suffix_header_repeated_merges_rows():
    lines = statement(split_at=10)
    index = next(i for i, l in enumerate(lines) if l.page == 4 and l.top == 50)
    lines.insert(index + 1, section_header(4, 55, "123456****91"))
    sections, _ = _split(lines)
    assert len(sections[0].rows) == 16


def test_section_with_rows_of_several_currencies_in_one_suffix_fails():
    lines = statement()
    index = next(i for i, l in enumerate(lines) if l.page == 4 and l.top == 70)
    lines[index] = row(4, 70, *ROWS_91[10][:4], ROWS_91[10][4], cur="USD")
    with pytest.raises(SplitError, match="currency"):
        _split(lines)
