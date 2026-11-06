"""Parser tests; every row is fictional (carrier fields are fake markers, never a real layout copy)."""
import json
from datetime import date
from decimal import Decimal

import pytest

from worker.einvoice_csv import ParseError, parse, period_of, to_payload

H1 = "表頭=M|載具名稱|載具號碼|發票日期|商店統編|商店店名|發票號碼|總金額|發票狀態|"
H2 = "明細=D|發票號碼|小計|品項名稱|"
M1 = "M|測試載具|/FAKE+01|20261005|12345678|測試商店|AB00000001|150|開立|"
D1 = "D|AB00000001|100|測試品A|"
D2 = "D|AB00000001|50|測試品B|"
M2 = "M|測試載具|/FAKE+01|20261006|87654321|範例店|CD00000002|30|開立|"
D3 = "D|CD00000002|30|範例品|"


def csv_bytes(*rows, bom=True, newline="\n", headers=(H1, H2)):
    text = newline.join([*headers, *rows]) + newline
    return (("﻿" if bom else "") + text).encode("utf-8")


def code(data):
    with pytest.raises(ParseError) as ei:
        parse(data)
    assert str(ei.value) == ei.value.code
    return ei.value.code


def test_parses_invoices_and_items():
    r = parse(csv_bytes(M1, D1, D2, M2, D3))
    assert [i.number for i in r] == ["AB00000001", "CD00000002"]
    assert r[0].issue_date == date(2026, 10, 5)
    assert r[0].total_amount == Decimal("150") and r[0].status == "開立"
    assert r[0].seller_tax_id == "12345678" and r[0].seller_name == "測試商店"
    assert [(x.seq, x.name, x.subtotal) for x in r[0].items] == [(1, "測試品A", Decimal("100")), (2, "測試品B", Decimal("50"))]
    assert r[1].items[0].seq == 1


def test_drops_carrier_fields():
    r = parse(csv_bytes(M1, D1))
    assert "測試載具" not in repr(r) and "/FAKE+01" not in repr(r)
    payloads = [to_payload(i, source_drive_id="drive-fake-1") for i in r]
    assert "/FAKE+01" not in json.dumps(payloads, ensure_ascii=False, default=str)
    assert set(payloads[0]) == {"number", "issue_date", "seller_tax_id", "seller_name", "total_amount", "status",
                                "source_drive_id", "items"}
    assert payloads[0]["issue_date"] == "2026-10-05" and payloads[0]["total_amount"] == "150"
    assert payloads[0]["items"] == [{"seq": 1, "name": "測試品A", "subtotal": "100"}]
    json.dumps(payloads)


def test_crlf_blank_lines_and_bom():
    base = parse(csv_bytes(M1, D1, D2))
    assert parse(csv_bytes(M1, D1, D2, newline="\r\n")) == base
    assert parse(csv_bytes(M1, "", D1, D2, "", "", bom=False)) == base


def test_item_number_mismatch_fails_file():
    assert code(csv_bytes(M1, "D|ZZ00000009|1|x|")) == "item_number"


def test_orphan_item_fails_file():
    assert code(csv_bytes(D1, M1)) == "orphan_item"


def test_bad_header_date_amount_number():
    assert code(csv_bytes(M1, headers=(H1, "bad"))) == "header"
    assert code(csv_bytes(M1, headers=("bad", H2))) == "header"
    assert code(csv_bytes(M1.replace("20261005", "20261305"))) == "date"
    assert code(csv_bytes(M1.replace("20261005", "2026-10-05"))) == "date"
    assert code(csv_bytes(M1.replace("|150|", "|abc|"))) == "amount"
    assert code(csv_bytes(M1.replace("|150|", "|NaN|"))) == "amount"
    assert code(csv_bytes(M1.replace("AB00000001", "ab1"))) == "number"


def test_row_shape_and_encoding():
    assert code(csv_bytes("M|a|b|")) == "row_shape"
    assert code(csv_bytes(M1, "D|AB00000001|5|")) == "row_shape"
    assert code(csv_bytes(M1, "X|1|")) == "row_shape"
    assert code(b"\xff\xfe\x00bad") == "encoding"


def test_item_name_with_pipe():
    r = parse(csv_bytes(M1, "D|AB00000001|50|測試|品項|"))
    assert r[0].items[0].name == "測試|品項"


def test_foreign_decimal_total():
    r = parse(csv_bytes("M|測試載具|/FAKE+01|20261005||FOREIGN|AB00000003|20.99|開立|"))
    assert r[0].total_amount == Decimal("20.99") and r[0].seller_tax_id == ""


def test_repeat_in_one_file_last_wins():
    r = parse(csv_bytes(M1, D1, M2, D3, M1, D2))
    assert [i.number for i in r] == ["CD00000002", "AB00000001"]
    assert [x.name for x in r[1].items] == ["測試品B"]


def test_repeat_in_one_file_with_another_date_in_the_period_is_one_invoice():
    # the API's identity is (number, period): 10-05 and 09-28 are both period 11509 (Sep-Oct)
    later = "M|測試載具|/FAKE+01|20260928|12345678|測試商店|AB00000001|99|開立|"
    r = parse(csv_bytes(M1, D1, later, "D|AB00000001|99|測試品C|"))
    assert len(r) == 1
    assert (r[0].issue_date, r[0].total_amount, [x.name for x in r[0].items]) == (date(2026, 9, 28), Decimal("99"), ["測試品C"])


def test_same_number_in_another_period_stays_apart():
    other = "M|測試載具|/FAKE+01|20261205|12345678|測試商店|AB00000001|99|開立|"
    r = parse(csv_bytes(M1, D1, other, "D|AB00000001|99|測試品C|"))
    assert [i.issue_date for i in r] == [date(2026, 10, 5), date(2026, 12, 5)]


def test_period_of_matches_the_api():
    from datetime import timedelta

    from app.services.einvoice_rules import period_of as api_period_of
    day = date(2024, 1, 1)
    while day <= date(2027, 12, 31):
        assert period_of(day) == api_period_of(day), day
        day += timedelta(days=1)
    for day in (date(1912, 1, 1), date(2011, 12, 31), date(2111, 2, 28)):
        assert period_of(day) == api_period_of(day), day


@pytest.mark.parametrize("bad", ["1,200", "1e5", "Infinity", "NaN", "１２０", "12.34567", "12345678901234567"])
def test_amount_forms_refused(bad):
    assert code(csv_bytes(M1.replace("|150|", f"|{bad}|"))) == "amount"


def test_amount_limits_accepted():
    r = parse(csv_bytes(M1.replace("|150|", "|-1234567890123456.1234|")))
    assert r[0].total_amount == Decimal("-1234567890123456.1234")


def test_api_bounds_enforced():
    assert code(csv_bytes(M1, "D|AB00000001|5||")) == "row_shape"
    assert code(csv_bytes(M1, "D|AB00000001|5|" + "x" * 257 + "|")) == "row_shape"
    assert code(csv_bytes(M1.replace("測試商店", ""))) == "row_shape"
    assert code(csv_bytes(M1.replace("測試商店", "x" * 129))) == "row_shape"
    assert code(csv_bytes(M1.replace("|開立|", "|" + "s" * 17 + "|"))) == "row_shape"
    assert code(csv_bytes(M1.replace("|開立|", "||"))) == "row_shape"
    assert code(csv_bytes(M1.replace("12345678", "12-45678"))) == "row_shape"
    assert code(csv_bytes(M1.replace("12345678", "1" * 17))) == "row_shape"


def test_too_many_items():
    assert len(parse(csv_bytes(M1, *[D1] * 500))[0].items) == 500
    assert code(csv_bytes(M1, *[D1] * 501)) == "row_shape"
