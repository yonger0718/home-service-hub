from datetime import date, time
from decimal import Decimal

import pytest

from app.services.moze_csv import MozeImportError, parse_moze_csv


def test_header_with_and_without_bom_and_crlf_is_accepted(moze):
    rows = [moze.opening("錢包", "TWD", "2000"), moze.row("錢包", "TWD", "支出", "-120", main="飲食")]
    for data in (moze.csv(*rows), moze.csv(*rows, bom=False, newline="\r\n")):
        parsed = parse_moze_csv(data)
        assert [r.amount for r in parsed.rows] == [Decimal("-120")]


def test_header_missing_column_is_rejected_naming_it(moze):
    header = moze.header.replace(",對象", "")
    with pytest.raises(MozeImportError, match="missing columns: 對象"):
        parse_moze_csv(moze.csv(header=header))


def test_header_with_unexpected_column_is_rejected_naming_it(moze):
    with pytest.raises(MozeImportError, match="unexpected columns: 備註"):
        parse_moze_csv(moze.csv(header=moze.header + ",備註"))


def test_record_types_map_to_kinds_including_refund(moze):
    types = {
        "支出": "expense", "收入": "income", "轉出": "transfer_out", "轉入": "transfer_in",
        "應收款項": "receivable", "應付款項": "payable", "餘額調整": "balance_adjustment",
        "手續費": "fee", "折扣": "discount", "紅利回饋": "reward", "利息": "interest", "退款": "refund",
    }
    data = moze.csv(
        moze.opening("錢包", "TWD", "0"),
        *[moze.row("錢包", "TWD", record_type, "350", main=record_type) for record_type in types],
    )
    parsed = parse_moze_csv(data)
    assert [r.kind for r in parsed.rows] == list(types.values())
    assert parsed.rows[-1].amount == Decimal("350")


def test_initial_amount_sets_opening_balance_without_a_row(moze):
    parsed = parse_moze_csv(moze.csv(moze.opening("去日本的錢", "JPY", "180000")))
    assert parsed.rows == ()
    assert parsed.row_count == 1
    spec = parsed.accounts["去日本的錢"]
    assert (spec.currency, spec.opening_balance) == ("JPY", Decimal("180000"))


def test_unknown_record_type_fails_naming_value_and_row(moze):
    data = moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "測試", "-1"))
    with pytest.raises(MozeImportError, match="row 3: unknown 記錄類型 '測試'"):
        parse_moze_csv(data)


def test_missing_opening_balance_row_fails_naming_account(moze):
    data = moze.csv(moze.row("Line Bank", "TWD", "支出", "-1", main="飲食"))
    with pytest.raises(MozeImportError, match="account 'Line Bank' has no 初始金額 row"):
        parse_moze_csv(data)


def test_duplicate_opening_balance_rows_fail_naming_account_and_rows(moze):
    data = moze.csv(moze.opening("錢包", "TWD", "2000"), moze.opening("錢包", "TWD", "1500"))
    with pytest.raises(MozeImportError, match="account '錢包' has 2 初始金額 rows: 2, 3"):
        parse_moze_csv(data)


def test_account_currency_comes_from_its_opening_row(moze):
    data = moze.csv(
        moze.row("華航卡", "JPY", "支出", "-1800", main="飲食"),
        moze.opening("華航卡", "TWD", "0"),
        moze.row("華航卡", "TWD", "支出", "-100", main="飲食"),
    )
    parsed = parse_moze_csv(data)
    assert parsed.accounts["華航卡"].currency == "TWD"
    assert [(r.currency, r.amount) for r in parsed.rows] == [("JPY", Decimal("-1800")), ("TWD", Decimal("-100"))]


def test_field_rules_for_dates_times_decimals_and_tags(moze):
    data = moze.csv(
        moze.opening("錢包", "TWD", "0"),
        moze.row("錢包", "TWD", "支出", "-266", fee="-3", discount="", time="", tags="#午餐; #公司"),
    )
    row = parse_moze_csv(data).rows[0]
    assert row.entry_date == date(2026, 9, 1)
    assert row.entry_time is None
    assert (row.amount, row.fee, row.discount) == (Decimal("-266"), Decimal("-3"), Decimal("0"))
    assert row.tags == ("午餐", "公司")
    data = moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "支出", "-1", time="08:05"))
    assert parse_moze_csv(data).rows[0].entry_time == time(8, 5)


def test_invalid_date_fails_naming_row(moze):
    data = moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "支出", "-1", date="2026-09-01"))
    with pytest.raises(MozeImportError, match="row 3: invalid 日期"):
        parse_moze_csv(data)


def test_quoted_multiline_description_keeps_record_row_numbers(moze):
    # Review focus: 268 real rows have multi-line 描述; row numbers count records, not lines.
    data = moze.csv(
        moze.opening("錢包", "TWD", "0"),
        moze.row("錢包", "TWD", "支出", "-1", description='"line one, with comma\nline two"'),
        moze.row("錢包", "TWD", "支出", "-2"),
        moze.row("錢包", "TWD", "測試", "-3"),
    )
    with pytest.raises(MozeImportError, match="row 5: unknown 記錄類型"):
        parse_moze_csv(data)
    ok = moze.csv(
        moze.opening("錢包", "TWD", "0"),
        moze.row("錢包", "TWD", "支出", "-1", description='"line one, with comma\nline two"'),
        moze.row("錢包", "TWD", "支出", "-2"),
    )
    rows = parse_moze_csv(ok).rows
    assert [r.row_no for r in rows] == [3, 4]
    assert rows[0].description == "line one, with comma\nline two"
