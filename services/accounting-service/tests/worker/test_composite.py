from decimal import Decimal

import pytest

from tests.worker.composite_fixture import (DECLARED, HOLDINGS, ROWS_91, L, W, X_BAL, X_OUT, amount, page_number_line, row,
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
    cases = [(both, "one amount column"), (neither, "one amount column"), (two_balances, "twice"),
             (no_currency, "no currency"), (trailing_word, "non-numeric"), (negative, "negative")]
    for bad, reason in cases:
        lines = statement()
        index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 110)
        lines[index] = bad
        with pytest.raises(SplitError, match=rf"91/TWD: row 0 .*{reason}"):
            _split(lines)


def test_a_foreign_row_in_a_twd_section_is_its_own_key_and_needs_a_holdings_row():
    # rev 9: rows are keyed by suffix AND currency; a stray JPY row under the 91 account has no 91/JPY holdings → fail closed
    lines = statement()
    index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 122)
    lines[index] = row(3, 122, *ROWS_91[1][:4], ROWS_91[1][4], cur="JPY")
    with pytest.raises(SplitError, match=r"91/JPY: no holdings row"):
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


def test_a_currency_without_a_holdings_row_fails_the_file():
    # rev 9: a second currency under one account is its own key; without a holdings row for it the file fails closed
    lines = statement()
    index = next(i for i, l in enumerate(lines) if l.page == 4 and l.top == 70)
    lines[index] = row(4, 70, *ROWS_91[10][:4], ROWS_91[10][4], cur="USD")
    with pytest.raises(SplitError, match=r"91/USD: no holdings row"):
        _split(lines)


def test_holdings_marker_inside_the_transaction_area_fails():
    lines = statement()
    index = next(i for i, l in enumerate(lines) if l.page == 4 and l.top == 190)
    lines.insert(index, L(4, 189, W("持有明細", 40)))
    with pytest.raises(SplitError, match="holdings block"):
        _split(lines)


def test_repeated_header_geometry_is_re_recorded_per_page():
    lines = [_shift(l) if l.page == 4 else l for l in statement()]
    sections, _ = _split(lines)
    assert len(sections[0].rows) == 16 and len(sections[1].rows) == 12


def _shift(line, dx=50):
    words = tuple(type(w)(w.text, w.x0 + dx, w.x1 + dx) for w in line.words)
    return L(line.page, line.top, *words)


def test_unreadable_holdings_amount_and_impossible_date_are_split_errors():
    lines = statement()
    lines.insert(10, holding_line("-,"))
    with pytest.raises(SplitError, match="holdings amount"):
        _split(lines)
    lines = statement()
    index = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 110)
    lines[index] = row(3, 110, "2026/02/30", "交割股款", "2,000", None, "28,000")
    with pytest.raises(SplitError, match="91/TWD: row 0 .*date"):
        _split(lines)


def holding_line(amount_text):
    from tests.worker.composite_fixture import holding
    return holding(2, 170, "活期存款", "123456****77", "屏東分行", amount_text)


def test_section_without_holdings_or_without_rows_fails():
    lines = statement()
    lines += [section_header(4, 300, "123456****77"), row(4, 312, "2026/09/01", "交割股款", "1", None, "1")]
    with pytest.raises(SplitError, match="77/TWD: no holdings row"):
        _split(lines)
    lines = statement() + [section_header(4, 300, "123456****77")]
    with pytest.raises(SplitError, match="without rows"):
        _split(lines)


from worker import mask, passwords
from worker.composite import PARSER_ANCHOR, PARSER_ROWS, Row, bodies, line_kind


def _row(desc, out=None, inn=None, cur="TWD"):
    from datetime import date as _d
    return Row(_d(2026, 9, 1), desc, cur, Decimal(out) if out else None, Decimal(inn) if inn else None, Decimal(1))


def test_line_kind_is_direction_first_and_exact_match_only():
    assert line_kind(_row("交割股款", out="10")) == "withdrawal"
    assert line_kind(_row("薪資獎金", inn="10")) == "deposit"
    assert line_kind(_row("存款利息", inn="10")) == "interest"
    assert line_kind(_row(" 利息 ", inn="10")) == "interest"
    assert line_kind(_row("存款　利息", inn="10")) == "deposit"  # NFKC turns U+3000 into a space; internal spaces survive, so no table hit
    assert line_kind(_row("手續費", out="10")) == "fee"
    assert line_kind(_row("轉帳備註含利息", inn="10")) == "deposit"
    assert line_kind(_row("手續費退回", inn="10")) == "deposit"
    assert line_kind(_row("利息", out="10")) == "withdrawal"
    assert line_kind(_row("利息", inn="0")) == "unknown"


def _masker(names=()):
    return mask.Masker(passwords.Identity(None, None, list(names)))


def test_bodies_sign_amounts_by_the_present_cell_and_mask_descriptions():
    sections, _ = _split()
    targets = {"91/TWD": {"account_id": 67, "anchor_only": False}, "59/TWD": {"account_id": 76, "anchor_only": False},
               "68/TWD": {"account_id": 68, "anchor_only": True}, "50/JPY": {"account_id": 81, "anchor_only": True}}
    out = dict(bodies(sections, file_id=5, targets=targets, parser_version="v1", masker=_masker(), candidates=[]))
    body = out["91/TWD"]
    assert body["file_id"] == 5 and body["account_id"] == 67 and body["kind"] == "bank" and body["parser"] == PARSER_ROWS
    assert body["parser_version"] == "v1" and body["currency"] == "TWD"
    assert body["period_start"] == "2026-09-01" and body["period_end"] == "2026-09-30" == body["closing_date"]
    assert body["opening_balance"] == "30000" and body["statement_total"] == "34431" and body["minimum_payment"] is None
    assert body["raw"] == {"section": "91/TWD", "anchor": False, "rows": 16}
    lines = body["lines"]
    assert [l["seq"] for l in lines] == list(range(1, 17))
    assert lines[0]["printed_amount"] == "-2000" and lines[0]["line_kind"] == "withdrawal"
    assert lines[1]["printed_amount"] == "1500" and lines[1]["line_kind"] == "deposit"
    assert lines[12]["line_kind"] == "interest" and lines[15]["line_kind"] == "fee" and lines[15]["printed_amount"] == "-200"
    assert all(l["txn_date"] == l["posted_date"] and l["is_subtotal"] is False and l["foreign_amount"] is None for l in lines)
    anchor = out["68/TWD"]
    assert anchor["parser"] == PARSER_ANCHOR and anchor["lines"] == [] and anchor["opening_balance"] == anchor["statement_total"] == "0"
    assert anchor["raw"] == {"section": "68/TWD", "anchor": True, "rows": 0}
    assert out["50/JPY"]["currency"] == "JPY" and out["50/JPY"]["account_id"] == 81
    assert "50/USD" not in out


def test_bodies_mask_candidates_and_configured_names_in_descriptions():
    rows = [("2026/09/01", "轉帳 王小明 1234", None, "1,000", "31,000"), ("2026/09/02", "交割股款", "1,000", None, "30,000")]
    sections, _ = _split(statement(rows_91=rows, holdings={**HOLDINGS, "91/TWD": "30,000"}, split_at=2))
    targets = {"91/TWD": {"account_id": 67, "anchor_only": False}, "59/TWD": {"account_id": 76, "anchor_only": False},
               "68/TWD": {"account_id": 68, "anchor_only": True}, "50/JPY": {"account_id": 81, "anchor_only": True}}
    out = dict(bodies(sections, file_id=5, targets=targets, parser_version="v1", masker=_masker(["王小明"]),
                      candidates=["1234"]))
    merchant = out["91/TWD"]["lines"][0]["merchant_raw"]
    assert "王小明" not in merchant and "1234" not in merchant and "轉帳" in merchant


def test_bodies_classify_before_masking_and_mask_long_digit_runs():
    rows = [("2026/09/01", "利息", None, "1,000", "31,000"), ("2026/09/02", "轉帳 1234567890", "1,000", None, "30,000")]
    sections, _ = _split(statement(rows_91=rows, holdings={**HOLDINGS, "91/TWD": "30,000"}, split_at=2))
    targets = {"91/TWD": {"account_id": 67, "anchor_only": False}, "59/TWD": {"account_id": 76, "anchor_only": False},
               "68/TWD": {"account_id": 68, "anchor_only": True}, "50/JPY": {"account_id": 81, "anchor_only": True}}
    lines = dict(bodies(sections, file_id=5, targets=targets, parser_version="v1", masker=_masker(["利息"]),
                        candidates=[]))["91/TWD"]["lines"]
    assert lines[0]["line_kind"] == "interest" and lines[0]["merchant_raw"] == "[NAME]"  # classified on the original, then masked
    assert "1234567890" not in lines[1]["merchant_raw"] and "[NUM…7890]" in lines[1]["merchant_raw"]


def test_bodies_zero_row_is_unknown_and_cancelling_rows_stay():
    rows = [("2026/09/01", "利息", None, "0", "30,000"), ("2026/09/02", "交割股款", "10", None, "29,990"),
            ("2026/09/03", "交割股款", None, "10", "30,000")]
    sections, _ = _split(statement(rows_91=rows, holdings={**HOLDINGS, "91/TWD": "30,000"}, split_at=3))
    targets = {"91/TWD": {"account_id": 67, "anchor_only": False}, "59/TWD": {"account_id": 76, "anchor_only": False},
               "68/TWD": {"account_id": 68, "anchor_only": True}, "50/JPY": {"account_id": 81, "anchor_only": True}}
    lines = dict(bodies(sections, file_id=5, targets=targets, parser_version="v1", masker=_masker(), candidates=[]))["91/TWD"]["lines"]
    assert lines[0]["printed_amount"] == "0" and lines[0]["line_kind"] == "unknown"
    assert [l["printed_amount"] for l in lines[1:]] == ["-10", "10"]


def test_standalone_page_numbers_and_no_repeated_header_split_like_the_glued_layout():
    sections, report = _split(statement(standalone_page_numbers=True))
    assert [len(s.rows) for s in sections] == [16, 12, 0, 0] and report.anchor == 2


def test_standalone_page_number_between_sections_is_ignored():
    lines = statement(standalone_page_numbers=True)
    index = next(i for i, l in enumerate(lines) if l.text.startswith("活期存款 帳號") and l.page == 4)
    lines.insert(index, page_number_line(4, 400, 3))
    sections, _ = _split(lines)
    assert [len(s.rows) for s in sections] == [16, 12, 0, 0]


@pytest.mark.parametrize("n", [3, 12])
def test_standalone_page_number_never_counts_as_a_row(n):
    lines = statement(standalone_page_numbers=True)
    index = next(i for i, l in enumerate(lines) if l.page == 4 and l.top == 70)
    lines.insert(index, page_number_line(3, 818, n))
    sections, _ = _split(lines)
    assert len(sections[0].rows) == 16


FX_ROWS = [("2026/09/03", "外幣提領", "10,000", None, "140,000"), ("2026/09/20", "外幣存入", None, "10,000", "150,000")]


@pytest.mark.parametrize("standalone", [False, True])
def test_optional_fx_section_is_processed_and_otherwise_an_anchor(standalone):
    sections, report = _split(statement(fx_rows=FX_ROWS, standalone_page_numbers=standalone))
    by_key = {s.key: s for s in sections}
    assert len(by_key["50/JPY"].rows) == 2 and by_key["50/JPY"].opening_balance == Decimal("150000")
    assert by_key["50/JPY"].rows[0].currency == "JPY"
    assert report.anchor == 1 and len(by_key["68/TWD"].rows) == 0 and report.sections == 4
    sections, report = _split(statement(standalone_page_numbers=standalone))
    assert report.anchor == 2 and {s.key: s for s in sections}["50/JPY"].rows == ()


USD_ROWS = [("2026/09/10", "外幣存入", None, "1.00", "3.47", "USD"), ("2026/09/12", "外幣提領", "1.00", None, "2.47", "USD")]


def test_two_currencies_in_the_fx_section_are_two_keys():
    # rev 9: the FX account prints JPY and USD rows under one header; each currency is its own section with its own
    # balance chain against its own holdings row
    sections, report = _split(statement(fx_rows=FX_ROWS + USD_ROWS), {**DECLARED, "50/USD": False})
    by_key = {s.key: s for s in sections}
    assert len(by_key["50/JPY"].rows) == 2 and len(by_key["50/USD"].rows) == 2
    assert by_key["50/USD"].opening_balance == Decimal("2.47") and by_key["50/USD"].closing_balance == Decimal("2.47")
    assert by_key["50/USD"].rows[0].currency == "USD" and report.skipped_keys == [] and report.sections == 5


def test_an_undeclared_currency_of_a_declared_account_is_skipped_with_its_rows():
    sections, report = _split(statement(fx_rows=FX_ROWS + USD_ROWS))  # 50/USD not in the map
    by_key = {s.key: s for s in sections}
    assert len(by_key["50/JPY"].rows) == 2 and "50/USD" not in by_key
    assert report.skipped_keys == ["50/USD"] and "50/USD" not in report.rows_per_key


def test_a_usd_only_month_keeps_the_jpy_anchor_and_skips_usd():
    sections, report = _split(statement(fx_rows=USD_ROWS))
    by_key = {s.key: s for s in sections}
    assert by_key["50/JPY"].rows == () and report.anchor == 2 and report.skipped_keys == ["50/USD"]


def test_a_broken_chain_in_one_currency_names_that_key():
    bad = [USD_ROWS[0], ("2026/09/12", "外幣提領", "1.00", None, "9.99", "USD")]
    with pytest.raises(SplitError, match=r"50/USD: running balance breaks"):
        _split(statement(fx_rows=FX_ROWS + bad), {**DECLARED, "50/USD": False})


def test_rows_of_an_undeclared_account_still_fail_the_file():
    declared = {k: v for k, v in DECLARED.items() if not k.startswith("59/")}
    with pytest.raises(SplitError, match=r"59/TWD: undeclared section with rows"):
        _split(statement(), declared)


def test_repr_never_prints_statement_content():
    sections, _ = _split()
    text = repr(sections[0]) + repr(sections[0].rows[0])
    assert "datetime.date" not in text
    for secret in ("28,000", "28000", "2000", "34431", "2026-09-01", "2026, 9", "2026-09-30", "交割股款", "30000"):
        assert secret not in text


def test_unmasked_account_number_is_redacted_from_messages_and_report():
    lines = statement()
    lines.insert(10, L(2, 170, W("活期存款", 40), W("9988776655", 120), W("屏東分行", 240), W("5", 400)))
    _, report = _split(lines)
    assert "?/TWD" in report.skipped_keys and "9988776655" not in report.skipped_keys
    assert "9988776655" not in repr(report)
    lines.insert(10, L(2, 171, W("活期存款", 40), W("9988776655", 120), W("屏東分行", 240), W("5", 400)))
    with pytest.raises(SplitError) as info:
        _split(lines)
    assert "9988776655" not in str(info.value)


def test_bare_suffix_holding_resolves_to_its_key():
    holdings = {**HOLDINGS, "91/TWD": "34,431"}
    lines = statement(holdings=holdings)
    lines[4] = L(2, 90, W("活期存款", 40), W("91", 120), W("屏東分行", 240), W("34,431", 400))
    sections, _ = _split(lines)
    assert sections[0].key == "91/TWD" and len(sections[0].rows) == 16


def test_dated_row_outside_the_area_or_without_the_area_marker_fails():
    lines = statement()
    first = next(l for l in lines if l.page == 3 and l.top == 110)
    marker = next(i for i, l in enumerate(lines) if l.text == "交易明細")
    before = [l for l in lines if l is not first]
    before.insert(marker, first)
    with pytest.raises(SplitError, match="outside"):
        _split(before)
    inside_holdings = [l for l in lines if l is not first]
    inside_holdings.insert(5, first)
    with pytest.raises(SplitError, match="outside"):
        _split(inside_holdings)
    without_marker = [l for l in lines if l.text != "交易明細"]
    with pytest.raises(SplitError, match="outside"):
        _split(without_marker, {k: True for k in DECLARED})


def test_dated_row_before_the_period_line_fails():
    """A dated row ahead of the statement's period line is still a row outside any section (AGENT-162 M1)."""
    lines = statement()
    first = next(i for i, l in enumerate(lines) if l.page == 3 and l.top == 110)
    lines.insert(0, lines.pop(first))
    with pytest.raises(SplitError, match="outside"):
        _split(lines)
    without_period = [l for l in lines if "綜合月對帳單" not in l.text]
    with pytest.raises(SplitError, match="period"):  # a missing period is still reported first
        _split(without_period)
