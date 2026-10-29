import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from tests.worker.pdfgen import encrypt, make_pdf
from worker.cathay_statement import StatementError, extract_text, parse_statement, to_payload

FIXTURES = Path(__file__).parent / "fixtures" / "cathay"
SHA = "0" * 63 + "1"
PORTFOLIO = Path(__file__).resolve().parents[3] / "stock-portfolio-service"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_bytes().decode("utf-8").replace("\r\n", "\n")


def payload(text: str, file_name: str = "2026-03-09_國泰證券_日對帳單_範例.pdf") -> dict:
    return to_payload(parse_statement(text), drive_file_id="fictional-file-id", sha256=SHA, file_name=file_name)


def code_of(text: str) -> str:
    with pytest.raises(StatementError) as err:
        parse_statement(text)
    return err.value.code


def test_single_line():
    st = parse_statement(load("single"))
    assert (st.trade_date.isoformat(), st.settlement_date.isoformat(), st.net_amount) == ("2026-03-09", "2026-03-11", Decimal(-12305))
    assert st.lines == [{"name": "範例電子", "category": "集買", "quantity": 1000, "price": Decimal("12.3"), "amount": 12300,
                         "fee": 5, "tax": 0, "net": -12305, "order_no": "XY12300", "points": False}]
    assert st.holdings == [{"code": "9999", "name": "範例電子", "shares": 1000}]
    assert not st.unsupported


def test_single_payload_matches_the_portfolio_fixture():
    assert payload(load("single")) == json.loads((PORTFOLIO / "tests/fixtures/cathay_statement_payload.json").read_text(encoding="utf-8"))


def test_two_pages_and_sell():
    st = parse_statement(load("two_pages"))
    assert [(ln["category"], ln["tax"], ln["net"]) for ln in st.lines] == [("集買", 0, -12305), ("集賣", 120, 39860)]
    assert [h["code"] for h in st.holdings] == ["9999", "99999", "999999"]
    assert st.totals == {"amount": 52300, "fee": 25, "tax": 120, "net": 27555}


def test_day_trade_pair_with_empty_holdings():
    st = parse_statement(load("pair"))
    assert [ln["category"] for ln in st.lines] == ["沖買", "沖賣"]
    assert st.holdings == [] and st.net_amount == 153


def test_points_prefix():
    st = parse_statement(load("points_paid"))
    assert st.lines[1]["points"] is True and st.lines[1]["order_no"] == "XY12301"
    assert st.lines[0]["points"] is False


def test_points_variant_without_prefixed_line():
    st = parse_statement(load("points"))
    assert st.lines[0]["points"] is False and st.net_amount == -12305


def test_points_paid_line_cash_net():
    st = parse_statement(load("points_paid"))
    assert [ln["points"] for ln in st.lines] == [False, True]
    assert st.lines[1]["net"] == -2862 and st.net_amount == -15167 == sum(ln["net"] for ln in st.lines)
    out = payload(load("points_paid"))
    assert out["net_amount"] == out["totals"]["net"] == "-15167"


def test_points_paid_cash_net_must_exclude_the_points_line():
    text = load("points_paid")
    assert code_of(text.replace("淨收付金額： -12,305", "淨收付金額： -15,167")) == "net_total"
    assert code_of(text.replace("0 0 -12,305", "0 0 -15,167").replace("淨收付金額： -12,305", "淨收付金額： -15,167")) == "net_total"


def test_points_line_needs_the_points_variant():
    text = load("single").replace("0 0 0 0 XY12300", "(小樹點數) 0 0 0 0 XY12300")
    assert code_of(text) == "net_total"


def test_one_order_in_two_price_groups():
    st = parse_statement(load("price_groups"))
    assert [(ln["order_no"], str(ln["price"])) for ln in st.lines] == [("XY12300", "12.3"), ("XY12301", "12.4")]


def test_unsupported_category():
    st = parse_statement(load("unsupported"))
    assert st.unsupported and st.lines == [] and st.holdings[0]["code"] == "9999"
    out = payload(load("unsupported"))
    assert out["lines"] == [] and out["warnings"] == ["unsupported_category"]


def test_unsupported_still_checks_header_and_footer():
    assert code_of(load("unsupported").replace("證券日對帳單 115年 3月 9日", "證券日對帳單 115年 3月 8日")) == "date_line"
    assert code_of(load("unsupported").replace("淨收付金額： -12,305", "淨收付金額： -12,306")) == "net_total"
    assert code_of(load("unsupported").replace("9999 範例電子", "12 範例電子")) == "holdings"


@pytest.mark.parametrize("fixture", ["single", "two_pages", "pair", "points", "points_paid", "price_groups", "unsupported"])
def test_payload_loads_into_the_portfolio_schema(fixture):
    path = PORTFOLIO / "app/schemas/cathay_statement.py"
    spec = importlib.util.spec_from_file_location("portfolio_cathay_statement_schema", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # pydantic resolves postponed annotations through sys.modules
    spec.loader.exec_module(module)
    model = module.StatementPayload.model_validate(payload(load(fixture)))
    assert model.unsupported == (fixture == "unsupported")


def test_header_and_date_line_disagree():
    assert code_of(load("single").replace("115年 3月 9日", "115年 3月 10日")) == "date_line"


def test_missing_header():
    assert code_of(load("single").replace("證券日對帳單", "對帳單", 1)) == "header"


def test_stray_line_in_item_section():
    assert code_of(load("two_pages").replace("範例機械 集賣", "備註 內容\n範例機械 集賣")) == "stray_line"


def test_line3_with_five_numbers():
    assert code_of(load("single").replace("0 0 0 0 XY12300", "0 0 0 0 0 XY12300")) == "line3"


def test_line4_not_zero():
    assert code_of(load("single").replace("XY12300\n0\n", "XY12300\n1\n")) == "line4"


def test_amount_off_by_two():
    assert code_of(load("single").replace("12.3 12,300", "12.3 12,302")) == "amount"


def test_net_identity_with_every_total_changed_to_match():
    # the fee moves on the item line and in 總合計; every net stays, so only the identity can catch it
    assert code_of(load("single").replace("12,300 5 0", "12,300 6 0")) == "net_identity"


def test_buy_tax_with_footer_tax_and_nets_unchanged():
    text = load("single").replace("12,300 5 0\n-12,305", "12,300 5 7\n-12,305").replace("總合計 12,300 5 0", "總合計 12,300 5 7")
    assert code_of(text) == "buy_tax"


def test_fee_total_differs():
    assert code_of(load("single").replace("總合計 12,300 5 0", "總合計 12,300 6 0")) == "totals"


def test_header_net_differs_from_footer_net():
    assert code_of(load("single").replace("淨收付金額： -12,305", "淨收付金額： -12,306")) == "net_total"
    assert code_of(load("single").replace("0 0 -12,305", "0 0 -12,306")) == "net_total"


def test_holdings_code_too_short():
    assert code_of(load("single").replace("9999 範例電子", "12 範例電子")) == "holdings"


@pytest.mark.parametrize("old,new,code", [
    ("1,000 12.3 12,300", "0 12.3 12,300", "item_grammar"),
    ("1,000 12.3 12,300", "1.5 12.3 12,300", "item_grammar"),
    ("1,000 12.3 12,300", "1,000 0 12,300", "item_grammar"),
    ("12.3 12,300 5 0", "12.3 -1 5 0", "item_grammar"),
    ("12.3 12,300 5 0", "12.3 1.5 5 0", "item_grammar"),
    ("12,300 5 0\n", "12,300 -1 0\n", "item_grammar"),
    ("12,300 5 0\n", "12,300 1.5 0\n", "item_grammar"),
    ("12,300 5 0\n", "12,300 5 -1\n", "item_grammar"),
    ("12,300 5 0\n", "12,300 5 1.5\n", "item_grammar"),
    ("XY12300", "XY1230", "line3"),
    ("XY12300", "XY123000", "line3"),
    ("XY12300", "XY-2300", "line3"),
    ("▲12.35 1,000 12,350", "▲12.35 -1 12,350", "holdings"),
])
def test_item_field_negatives(old, new, code):
    text = load("single")
    assert old in text
    assert code_of(text.replace(old, new, 1)) == code


def test_points_footer_differs_from_header_points():
    assert code_of(load("points").replace("淨收付點數： +3", "淨收付點數： +4")) == "net_total"
    assert code_of(load("points").replace("淨收付點數： +3\n", "")) == "net_total"
    assert code_of(load("single").replace("*表示", "淨收付點數： +3\n*表示")) == "net_total"


def test_greeting_missing():
    assert code_of(load("single").replace("親愛的 王小明 先生/小姐 您好:\n", "")) == "header"


def test_greeting_without_name_is_fine():
    assert parse_statement(load("single").replace("親愛的 王小明 先生/小姐", "親愛的 先生/小姐")).net_amount == -12305


def test_item_column_header_missing():
    assert code_of(load("single").replace("客戶應收付額\n", "")) == "header"


def test_buy_total_off_by_one():
    assert code_of(load("single").replace("買進總計： 12,300", "買進總計： 12,301")) == "totals"
    assert code_of(load("two_pages").replace("賣出總計： 40,000", "賣出總計： 40,001")) == "totals"


def test_nonzero_margin_totals_on_supported_statement():
    assert code_of(load("single").replace("0 0 0 0\n0\n買進", "0 34,000 0 0\n0\n買進")) == "totals"


def test_empty_line_inside_item_section():
    assert code_of(load("two_pages").replace("XY12300\n0\n", "XY12300\n0\n\n")) == "stray_line"


def test_optional_commas_and_signs():
    text = load("single").replace("0 0 -12,305", "0 0 -12305")
    assert parse_statement(text).net_amount == -12305


def test_link_line_variant_and_trailing_blank_lines():
    text = load("single").replace("智慧選股 台股行情 申購股票 熱門新聞", "智慧選股 除權除息表 股東會時間表 申購股票 理財行事曆 申報轉讓") + "\n\n"
    assert parse_statement(text).holdings[0]["code"] == "9999"


def test_filename_date_warning_only():
    same = payload(load("single"), "2026-03-09_國泰證券_日對帳單_範例.pdf")
    other = payload(load("single"), "2026-10-20_國泰證券_日對帳單_範例.pdf")
    assert same["warnings"] == [] and other["warnings"] == ["filename_date_differs"]
    other["warnings"] = []
    other["source"]["file_name"] = same["source"]["file_name"]
    assert other == same


def test_extract_text_layout():
    page1 = ["Statement line one", "Statement line two"]
    page2 = ["Page two line one"]
    data = encrypt(make_pdf([page1, page2]), "secret")
    text = extract_text(data, "secret")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    assert lines == page1 + page2
    assert "two\nPage two" in text.replace("\r\n", "\n")


ALL_FIXTURES = ["single", "two_pages", "pair", "points", "points_paid", "price_groups", "unsupported"]


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_any_truncation_raises_statement_error(fixture):
    lines = load(fixture).split("\n")
    while not lines[-1].strip():
        lines.pop()  # the link line is the last statement line: every shorter text is truncated
    for k in range(len(lines)):
        with pytest.raises(StatementError):
            parse_statement("\n".join(lines[:k]))


def test_shifted_item_line_is_not_unsupported():
    text = load("single").replace("範例電子 集買 1,000 12.3 12,300 5 0", "範例 電子 集買 1,000 12.3 12,300 5")
    assert code_of(text) == "item_grammar"


def test_unsupported_margin_totals_must_still_be_numbers():
    assert code_of(load("unsupported").replace("0 34,000 0 0", "0 x 0 0")) == "totals"
    assert code_of(load("unsupported").replace("0 34,000 0 0\n0\n", "0 34,000 0 0\nx\n")) == "totals"
    assert parse_statement(load("unsupported")).unsupported


def test_unsupported_section_lines_are_still_checked():
    base = load("unsupported")
    assert parse_statement(base).unsupported
    assert code_of(base.replace("原資買 1,000 12.1 12,100 5 XY88800", "原資買 範例電子 free text")) == "stray_line"
    assert code_of(base.replace("\n總合計 ", "\nany extra line\n總合計 ")) == "stray_line"
    assert code_of(base.replace("0 23 XY99900", "0 23 x XY99900")) == "line3"
    assert code_of(base.replace("-12,305\n0 23", "12,305\n0 23")) == "item_grammar"
    assert code_of(base.replace("XY99900\n0\n", "XY99900\nx\n")) == "line4"
    assert code_of(base.replace("範例電子 資買 1,000 12.3 12,300 5 0", "範例電子 資買 1,000 x 12,300 5 0")) == "item_grammar"

def test_footer_prepaid_and_retained_must_be_zero():
    assert code_of(load("single").replace("預收款： 0 留置款： 0", "預收款： 1 留置款： 0")) == "totals"
    assert code_of(load("single").replace("預收款： 0 留置款： 0", "預收款： 0 留置款： 1")) == "totals"


def test_signs_are_required_except_on_footer_line_two():
    pos = load("pair")
    assert parse_statement(pos.replace("\n153\n", "\n+153\n")).net_amount == 153  # footer line 2 may carry a plus
    assert code_of(pos.replace("0 0 +153", "0 0 153")) == "date_line"
    assert code_of(pos.replace("淨收付金額： +153", "淨收付金額： 153")) == "totals"
    assert code_of(pos.replace("應收付金額： +153", "應收付金額： 153")) == "totals"
    assert code_of(pos.replace("\n+12,458\n", "\n12,458\n")) == "item_grammar"
    assert code_of(load("points").replace("淨收付點數： +3", "淨收付點數： 3")) == "totals"
    assert code_of(load("points").replace("\n+3\n", "\n3\n")) == "date_line"
    assert parse_statement(pos.replace("0 0 +153", "0 0 +153")).net_amount == 153
